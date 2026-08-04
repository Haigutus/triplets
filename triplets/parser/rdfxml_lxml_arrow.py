"""RDF/XML parser engine: lxml → Arrow, fully compliant capture.

The reference engine of the ``parser_rdfxml`` kind — the RDF-compliant
sibling of the CIM parser. **Parse = pure capture, zero policy**: every
RDF-visible detail lands either in the base columns or the ``context``
struct; interpretation, validation and erroring are downstream work. The
parser itself never warns about or drops RDF constructs (withdrawn
attributes like ``rdf:aboutEach`` are captured raw in ``rdf_attributes``).

Base columns carry the same cleaned values the CIM parser produces
(``clean_rules`` defaults to clean_ID's chain; ``shorten_resources`` keeps
the fragment rule), so CIM tooling reads the frame unchanged — the context
struct makes every cleaning reversible: ``PREFIX + column`` reconstructs
the full term byte-exact.

Context struct fields — UPPERCASE reconstructs a base column by
concatenation, lowercase ``rdf_*`` is RDF term metadata:

    ID_PREFIX, KEY_PREFIX, VALUE_PREFIX      ("_:" marks blank nodes)
    rdf_value_kind   {"iri","blank","literal"}
    rdf_language     xml:lang, scoped inheritance ("" cancels)
    rdf_datatype     full datatype IRI (rdf:XMLLiteral for parseType=Literal)
    rdf_id_source    {"ID","about","nodeID","minted"}
    rdf_node_id      original author nodeID label (subjects are remapped to
                     minted uuids for collision-free scoping; label preserved)
    rdf_parse_type   raw rdf:parseType value on rows it produced
    rdf_attributes   JSON catch-all of unrecognized rdf:*/other attributes
    source_line      lxml sourceline (int32)

Blank nodes: minted ``uuid4`` ids with ``ID_PREFIX="_:"``; author
``rdf:nodeID`` labels remapped per document (label kept in ``rdf_node_id``).
"""
import json
import logging
import uuid as uuid_module

from urllib.parse import urljoin

from lxml import etree

from .utils import DEFAULT_CLEAN_RULES, clean_with_prefix

logger = logging.getLogger(__name__)

RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_RDF = "{" + RDF_NS + "}"
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

# attributes with dedicated handling — everything else is captured raw
_HANDLED = {_RDF + "ID", _RDF + "about", _RDF + "nodeID", _RDF + "resource",
            _RDF + "datatype", _RDF + "parseType", _XML_LANG,
            "{http://www.w3.org/XML/1998/namespace}base"}

CONTEXT_FIELDS = ("ID_PREFIX", "KEY_PREFIX", "VALUE_PREFIX",
                  "rdf_value_kind", "rdf_language", "rdf_datatype",
                  "rdf_id_source", "rdf_node_id", "rdf_parse_type",
                  "rdf_attributes", "source_line")


def context_struct_type():
    import pyarrow as pa
    return pa.struct([(name, pa.int32() if name == "source_line" else pa.string())
                      for name in CONTEXT_FIELDS])


class _State:
    """Per-document parse state: output columns + cleaning config + bnode map."""

    def __init__(self, instance_id, clean_rules, shorten_resources):
        rules = {**DEFAULT_CLEAN_RULES, **(clean_rules or {})}
        self.id_rules = tuple(rules.get("ID", ()))
        self.value_rules = tuple(rules.get("VALUE", ()))
        self.shorten = shorten_resources
        self.instance_id = instance_id
        self.node_ids = {}                       # author nodeID label → minted uuid
        self.columns = {name: [] for name in ("ID", "KEY", "VALUE", "INSTANCE_ID")}
        self.context = {name: [] for name in CONTEXT_FIELDS}

    def row(self, id_local, key, value, *, id_prefix=None, key_prefix=None,
            value_prefix=None, kind=None, lang=None, datatype=None,
            id_source=None, node_id=None, parse_type=None, attributes=None,
            line=None):
        self.columns["ID"].append(id_local)
        self.columns["KEY"].append(key)
        self.columns["VALUE"].append(value)
        self.columns["INSTANCE_ID"].append(self.instance_id)
        context = self.context
        context["ID_PREFIX"].append(id_prefix)
        context["KEY_PREFIX"].append(key_prefix)
        context["VALUE_PREFIX"].append(value_prefix)
        context["rdf_value_kind"].append(kind)
        context["rdf_language"].append(lang)
        context["rdf_datatype"].append(datatype)
        context["rdf_id_source"].append(id_source)
        context["rdf_node_id"].append(node_id)
        context["rdf_parse_type"].append(parse_type)
        context["rdf_attributes"].append(attributes)
        context["source_line"].append(line)


_XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"


def _defrag(base):
    return base.split("#", 1)[0] if base else base


def _rebase(element, base):
    """Scoped xml:base: the element's own attribute (resolved against the
    outer base) replaces it. Bases are kept fragment-free so resolving a
    "#ref" is a plain concat (the RFC 3986 result), not a urljoin."""
    own = element.get(_XML_BASE)
    if own is None:
        return base
    return _defrag(urljoin(base, own) if base else own)


def _resolve(base, reference):
    """Base-resolve a reference (base is threaded through the emitters —
    lxml's element.base walks the ancestor chain per call, too slow)."""
    if base is None or reference.startswith(("http://", "https://", "urn:")):
        return reference
    if reference.startswith("#"):
        return base + reference
    return urljoin(base, reference)


def _split_fragment(term):
    """(local, prefix) at the last '#' of an IRI — any scheme, including
    relative document bases (zip members), so fragment ids clean the same
    everywhere; the prefix keeps the strip lossless."""
    cut = term.rfind("#") + 1
    if cut:
        return term[cut:], term[:cut]
    return term, ""


def _clean_id(state, full):
    """Subject IRI → (local, prefix): fragment split (IDs are always cleaned,
    matching the CIM parser) then the static ID rules, prefixes concatenated."""
    local, prefix = _split_fragment(full)
    local, stripped = clean_with_prefix(local, state.id_rules)
    return local, (prefix + stripped) or None


def _clean_value(state, full):
    """Object IRI → (local, prefix): the shorten_resources fragment rule
    (when enabled) then the static VALUE rules."""
    local, prefix = _split_fragment(full) if state.shorten else (full, "")
    local, stripped = clean_with_prefix(local, state.value_rules)
    return local, (prefix + stripped) or None


def _subject(state, element, base):
    """Node element → (local, prefix, id_source, original_node_label)."""
    rdf_id = element.get(_RDF + "ID")
    if rdf_id is not None:
        local, prefix = _clean_id(state, _resolve(base, "#" + rdf_id))
        return local, prefix, "ID", None
    about = element.get(_RDF + "about")
    if about is not None:
        local, prefix = _clean_id(state, _resolve(base, about))
        return local, prefix, "about", None
    node = element.get(_RDF + "nodeID")
    if node is not None:
        minted = state.node_ids.setdefault(node, str(uuid_module.uuid4()))
        return minted, "_:", "nodeID", node
    return str(uuid_module.uuid4()), "_:", "minted", None


def _tag_parts(tag):
    """Clark-notation tag → (local name, namespace) — namespace kept verbatim
    so namespace + local == the full IRI (RDF/XML concatenation rule)."""
    if tag[0] == "{":
        cut = tag.rfind("}")
        return tag[cut + 1:], tag[1:cut]
    return tag, None


def _extra_attributes(element):
    """JSON catch-all of attributes with no dedicated handling AND no row of
    their own (withdrawn rdf:* constructs, anything future) — property
    attributes are materialized as literal rows instead (see
    _property_attribute_rows) and excluded here."""
    attrib = element.attrib
    if not attrib:
        return None
    extra = {name: value for name, value in attrib.items()
             if name not in _HANDLED and name.startswith("{" + RDF_NS + "}")}
    return json.dumps(extra, sort_keys=True) if extra else None


def _property_attributes(element):
    """Non-rdf, non-xml attributes on a node element are RDF property
    attributes: each is one literal triple about the subject."""
    return [(name, value) for name, value in element.attrib.items()
            if name not in _HANDLED and not name.startswith("{" + RDF_NS + "}")
            and not name.startswith("{http://www.w3.org/XML/1998/namespace}")]


def _language(element, inherited):
    """Scoped xml:lang: element's own tag wins; "" cancels; else inherited."""
    lang = element.get(_XML_LANG)
    if lang is None:
        return inherited
    return lang or None


def _emit_subject(state, element, subject, subject_prefix, id_source, node_label,
                  lang, base):
    """Emit one node element: Type row + property-attribute rows + one row
    per property element (nested node elements recurse as new subjects).
    *base* is the element's own in-scope xml:base (callers rebase)."""
    lang = _language(element, lang)
    type_local, type_ns = _tag_parts(element.tag)
    is_description = type_ns == RDF_NS and type_local == "Description"
    if not is_description:
        state.row(subject, "Type", type_local,
                  id_prefix=subject_prefix, value_prefix=type_ns, kind="iri",
                  id_source=id_source, node_id=node_label,
                  attributes=_extra_attributes(element), line=element.sourceline)

    emitted_any = not is_description
    # property attributes: literal triples in compact form
    for attribute_name, attribute_value in _property_attributes(element):
        attr_local, attr_ns = _tag_parts(attribute_name)
        state.row(subject, attr_local, attribute_value,
                  id_prefix=subject_prefix, key_prefix=attr_ns,
                  kind="literal", lang=lang, id_source=id_source,
                  node_id=node_label, line=element.sourceline)
        emitted_any = True

    emitted_any |= _emit_properties(state, element, subject, subject_prefix,
                                    id_source, node_label, lang, base)

    if not emitted_any and _extra_attributes(element) is not None:
        # a bare untyped node asserts no triples; emit a capture row only for
        # its unhandled rdf:* attributes (withdrawn constructs must not vanish)
        state.row(subject, "Type", "",
                  id_prefix=subject_prefix, kind=None, id_source=id_source,
                  node_id=node_label, attributes=_extra_attributes(element),
                  line=element.sourceline)


def _emit_properties(state, element, subject, subject_prefix, id_source,
                     node_label, lang, base):
    """Emit the property elements of one subject (also the body of
    parseType="Resource", where *element* is the property element itself)."""
    emitted_any = False
    li_counter = 0
    resource_key = _RDF + "resource"
    for prop in element.iterchildren(etree.Element):
        key_local, key_ns = _tag_parts(prop.tag)
        if key_ns == RDF_NS and key_local == "li":
            li_counter += 1
            key_local = f"_{li_counter}"     # per-parent numbering (RDF/XML spec)
        attrib = prop.attrib

        # the two dominant row shapes skip the full attribute machinery:
        if not attrib:
            if prop.text is not None:        # plain literal, no attributes
                state.row(subject, key_local, prop.text,
                          id_prefix=subject_prefix, key_prefix=key_ns,
                          kind="literal", lang=lang, id_source=id_source,
                          node_id=node_label, line=prop.sourceline)
                emitted_any = True
                continue
        elif len(attrib) == 1 and not len(prop):
            resource = attrib.get(resource_key)
            if resource is not None:         # pure reference (the CIM shape)
                local, prefix = _clean_value(state, _resolve(base, resource))
                state.row(subject, key_local, local,
                          id_prefix=subject_prefix, key_prefix=key_ns,
                          value_prefix=prefix, kind="iri", id_source=id_source,
                          node_id=node_label, line=prop.sourceline)
                emitted_any = True
                continue

        prop_lang = _language(prop, lang)
        prop_base = _rebase(prop, base)
        common = dict(id_prefix=subject_prefix, key_prefix=key_ns,
                      id_source=id_source, node_id=node_label,
                      attributes=_extra_attributes(prop), line=prop.sourceline)
        parse_type = prop.get(_RDF + "parseType")
        resource = prop.get(resource_key)
        prop_node = prop.get(_RDF + "nodeID")
        datatype = prop.get(_RDF + "datatype")
        reify = prop.get(_RDF + "ID")
        mark = len(state.columns["ID"])          # base-triple row index, for reification

        if parse_type == "Resource":
            # anonymous node whose properties are this element's children
            minted = str(uuid_module.uuid4())
            state.row(subject, key_local, minted, value_prefix="_:", kind="blank",
                      parse_type=parse_type, **common)
            _emit_properties(state, prop, minted, "_:", "minted", None,
                             prop_lang, prop_base)
        elif parse_type == "Collection":
            # rdf:List chain of minted blanks: first → member, rest → next/nil
            children = list(prop.iterchildren(etree.Element))
            heads = [str(uuid_module.uuid4()) for _ in children]
            links = dict(id_prefix="_:", key_prefix=RDF_NS, id_source="minted",
                         line=prop.sourceline)
            if not children:
                state.row(subject, key_local, "nil", value_prefix=RDF_NS,
                          kind="iri", parse_type=parse_type, **common)
            else:
                state.row(subject, key_local, heads[0], value_prefix="_:",
                          kind="blank", parse_type=parse_type, **common)
            for index, child in enumerate(children):
                child_base = _rebase(child, prop_base)
                member, member_prefix, member_source, member_label = _subject(state, child, child_base)
                state.row(heads[index], "first", member, value_prefix=member_prefix,
                          kind="blank" if member_prefix == "_:" else "iri", **links)
                _emit_subject(state, child, member, member_prefix,
                              member_source, member_label, prop_lang, child_base)
                if index + 1 < len(children):
                    state.row(heads[index], "rest", heads[index + 1],
                              value_prefix="_:", kind="blank", **links)
                else:
                    state.row(heads[index], "rest", "nil",
                              value_prefix=RDF_NS, kind="iri", **links)
        elif parse_type is not None:
            # "Literal" (or any unknown parseType, treated as Literal per spec):
            # the inner XML verbatim, tails included
            body = [prop.text or ""]
            for child in prop.iterchildren():
                body.append(etree.tostring(child, method="c14n", exclusive=True,
                                           with_tail=False).decode())
                body.append(child.tail or "")
            state.row(subject, key_local, "".join(body), kind="literal",
                      datatype=RDF_NS + "XMLLiteral",
                      parse_type=parse_type, **common)
        elif len(prop):
            # nested node element(s): each child is its own subject; this row
            # references it. rdf:type children become proper Type rows.
            for child in prop.iterchildren(etree.Element):
                child_base = _rebase(child, prop_base)
                child_subject, child_prefix, child_source, child_label = _subject(state, child, child_base)
                state.row(subject, key_local, child_subject,
                          value_prefix=child_prefix,
                          kind="blank" if child_prefix == "_:" else "iri", **common)
                _emit_subject(state, child, child_subject, child_prefix,
                              child_source, child_label, prop_lang, child_base)
        elif key_ns == RDF_NS and key_local == "type" and resource is not None:
            # rdf:type as a child element → a proper Type row (no lowercase dup)
            local, prefix = _clean_value(state, _resolve(prop_base, resource))
            state.row(subject, "Type", local,
                      id_prefix=subject_prefix, value_prefix=prefix, kind="iri",
                      id_source=id_source, node_id=node_label,
                      attributes=_extra_attributes(prop), line=prop.sourceline)
        elif resource is not None:
            local, prefix = _clean_value(state, _resolve(prop_base, resource))
            state.row(subject, key_local, local, value_prefix=prefix,
                      kind="iri", **common)
        elif prop_node is not None:
            minted = state.node_ids.setdefault(prop_node, str(uuid_module.uuid4()))
            state.row(subject, key_local, minted, value_prefix="_:", kind="blank",
                      **{**common, "node_id": prop_node})
        elif _property_attributes(prop):
            # empty property element with property attributes: blank object
            # carrying one literal row per attribute
            minted = str(uuid_module.uuid4())
            state.row(subject, key_local, minted, value_prefix="_:", kind="blank", **common)
            for attribute_name, attribute_value in _property_attributes(prop):
                attr_local, attr_ns = _tag_parts(attribute_name)
                state.row(minted, attr_local, attribute_value,
                          id_prefix="_:", key_prefix=attr_ns,
                          kind="literal", lang=prop_lang, id_source="minted",
                          line=prop.sourceline)
        else:
            state.row(subject, key_local, prop.text or "", kind="literal",
                      lang=prop_lang, datatype=datatype, **common)

        if reify is not None and len(state.columns["ID"]) > mark:
            # rdf:ID on a property element reifies its base triple (row *mark*)
            statement, statement_prefix = _clean_id(state, _resolve(prop_base, "#" + reify))
            object_context = {name: state.context[name][mark] for name in CONTEXT_FIELDS}
            links = dict(id_prefix=statement_prefix, key_prefix=RDF_NS,
                         id_source="ID", line=prop.sourceline)
            state.row(statement, "Type", "Statement", id_prefix=statement_prefix,
                      value_prefix=RDF_NS, kind="iri", id_source="ID",
                      line=prop.sourceline)
            state.row(statement, "subject", subject, value_prefix=subject_prefix,
                      kind="blank" if subject_prefix == "_:" else "iri", **links)
            state.row(statement, "predicate", key_local, value_prefix=key_ns,
                      kind="iri", **links)
            state.row(statement, "object", state.columns["VALUE"][mark],
                      value_prefix=object_context["VALUE_PREFIX"],
                      kind=object_context["rdf_value_kind"],
                      lang=object_context["rdf_language"],
                      datatype=object_context["rdf_datatype"], **links)
        emitted_any = True
    return emitted_any


def load_rdf_to_dataframe(path_or_fileobject, debug=False, clean_rules=None,
                          shorten_resources=True):
    """Parse one RDF/XML file → pyarrow.RecordBatch (4 base columns + context).

    See the module docstring for the capture contract and context fields.
    """
    import pyarrow as pa

    file_name = (path_or_fileobject if isinstance(path_or_fileobject, str)
                 else getattr(path_or_fileobject, "name", "<file-like>"))
    tree = etree.parse(path_or_fileobject,
                       etree.XMLParser(remove_blank_text=True, remove_comments=True,
                                       resolve_entities=True))
    root = tree.getroot()

    instance_id = str(uuid_module.uuid4())
    state = _State(instance_id, clean_rules, shorten_resources)

    # meta rows, identical shape to the CIM parser (null context)
    distribution_id = str(uuid_module.uuid4())
    nsmap_id = str(uuid_module.uuid4())
    state.row(distribution_id, "Type", "Distribution")
    state.row(distribution_id, "label", str(file_name))
    state.row(nsmap_id, "Type", "NamespaceMap")
    for prefix, uri in (root.nsmap or {}).items():
        state.row(nsmap_id, str(prefix) if prefix is not None else "", str(uri))
    if root.base:
        state.row(nsmap_id, "xml_base", root.base)

    root_lang = _language(root, None)
    root_base = _defrag(root.base)
    for node in root.iterchildren(etree.Element):
        node_base = _rebase(node, root_base)
        subject, prefix, id_source, node_label = _subject(state, node, node_base)
        _emit_subject(state, node, subject, prefix, id_source, node_label,
                      root_lang, node_base)

    base_arrays = [pa.array(state.columns[c], type=pa.string())
                   for c in ("ID", "KEY", "VALUE", "INSTANCE_ID")]
    context = pa.StructArray.from_arrays(
        [pa.array(state.context[f],
                  type=pa.int32() if f == "source_line" else pa.string())
         for f in CONTEXT_FIELDS],
        names=list(CONTEXT_FIELDS))
    return pa.RecordBatch.from_arrays(base_arrays + [context],
                                      names=["ID", "KEY", "VALUE", "INSTANCE_ID", "context"])
