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


def _resolve(element, reference):
    """Base-resolve a reference (lxml .base carries scoped xml:base, resolved)."""
    base = element.base
    return urljoin(base, reference) if base else reference


def _split_fragment(term):
    """(local, prefix) at the last '#' of an http(s)/base-resolved IRI."""
    if term.startswith(("http://", "https://")) and "#" in term:
        cut = term.rfind("#") + 1
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


def _subject(state, element):
    """Node element → (local, prefix, id_source, original_node_label)."""
    rdf_id = element.get(_RDF + "ID")
    if rdf_id is not None:
        local, prefix = _clean_id(state, _resolve(element, "#" + rdf_id))
        return local, prefix, "ID", None
    about = element.get(_RDF + "about")
    if about is not None:
        local, prefix = _clean_id(state, _resolve(element, about))
        return local, prefix, "about", None
    node = element.get(_RDF + "nodeID")
    if node is not None:
        minted = state.node_ids.setdefault(node, str(uuid_module.uuid4()))
        return minted, "_:", "nodeID", node
    return str(uuid_module.uuid4()), "_:", "minted", None


def _tag_parts(element):
    """Element tag → (local name, namespace) — namespace kept verbatim so
    namespace + local == the full IRI (RDF/XML concatenation rule)."""
    qname = etree.QName(element)
    return qname.localname, qname.namespace


def _extra_attributes(element):
    """JSON catch-all of attributes without dedicated handling (property
    attributes until Phase 2 materializes them, withdrawn rdf:* constructs,
    anything future) — capture-complete from day one."""
    extra = {name: value for name, value in element.attrib.items()
             if name not in _HANDLED}
    return json.dumps(extra, sort_keys=True) if extra else None


def _language(element, inherited):
    """Scoped xml:lang: element's own tag wins; "" cancels; else inherited."""
    lang = element.get(_XML_LANG)
    if lang is None:
        return inherited
    return lang or None


def _emit_subject(state, element, subject, subject_prefix, id_source, node_label, lang):
    """Emit one node element: Type row + one row per property element.

    Phase 1 scope: flat documents — nested node elements and parseType
    content produce a placeholder row (VALUE "") with everything about them
    captured in context; Phases 2-3 materialize them recursively.
    """
    lang = _language(element, lang)
    type_local, type_ns = _tag_parts(element)
    is_description = type_ns == RDF_NS and type_local == "Description"
    if not is_description:
        state.row(subject, "Type", type_local,
                  id_prefix=subject_prefix, value_prefix=type_ns, kind="iri",
                  id_source=id_source, node_id=node_label,
                  attributes=_extra_attributes(element), line=element.sourceline)
    elif _extra_attributes(element) is not None or id_source in ("nodeID", "minted"):
        # untyped node: no fake Type row, but its element-level capture
        # (attributes, blank-ness, line) must not be lost — carried on the
        # first property row below via the subject-level fields
        pass

    emitted_any = not is_description
    for prop in element.iterchildren(etree.Element):
        prop_lang = _language(prop, lang)
        key_local, key_ns = _tag_parts(prop)
        common = dict(id_prefix=subject_prefix, key_prefix=key_ns,
                      id_source=id_source, node_id=node_label,
                      attributes=_extra_attributes(prop), line=prop.sourceline)
        parse_type = prop.get(_RDF + "parseType")
        resource = prop.get(_RDF + "resource")
        prop_node = prop.get(_RDF + "nodeID")
        datatype = prop.get(_RDF + "datatype")

        if parse_type is not None:
            # Phases 2-3: Resource/Collection/Literal materialization
            state.row(subject, key_local, "", kind=None,
                      parse_type=parse_type, **common)
        elif len(prop):
            # Phase 2: nested node element(s)
            state.row(subject, key_local, "", kind=None, **common)
        elif resource is not None:
            local, prefix = _clean_value(state, _resolve(prop, resource))
            state.row(subject, key_local, local, value_prefix=prefix,
                      kind="iri", **common)
        elif prop_node is not None:
            minted = state.node_ids.setdefault(prop_node, str(uuid_module.uuid4()))
            state.row(subject, key_local, minted, value_prefix="_:", kind="blank",
                      **{**common, "node_id": prop_node})
        else:
            state.row(subject, key_local, prop.text or "", kind="literal",
                      lang=prop_lang, datatype=datatype, **common)
        emitted_any = True

    if not emitted_any:
        # a bare untyped node with no properties still exists as a subject —
        # keep it visible (rare; capture-complete)
        state.row(subject, "Type", "",
                  id_prefix=subject_prefix, kind=None, id_source=id_source,
                  node_id=node_label, attributes=_extra_attributes(element),
                  line=element.sourceline)


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
    for node in root.iterchildren(etree.Element):
        subject, prefix, id_source, node_label = _subject(state, node)
        _emit_subject(state, node, subject, prefix, id_source, node_label, root_lang)

    base_arrays = [pa.array(state.columns[c], type=pa.string())
                   for c in ("ID", "KEY", "VALUE", "INSTANCE_ID")]
    context = pa.StructArray.from_arrays(
        [pa.array(state.context[f],
                  type=pa.int32() if f == "source_line" else pa.string())
         for f in CONTEXT_FIELDS],
        names=list(CONTEXT_FIELDS))
    return pa.RecordBatch.from_arrays(base_arrays + [context],
                                      names=["ID", "KEY", "VALUE", "INSTANCE_ID", "context"])
