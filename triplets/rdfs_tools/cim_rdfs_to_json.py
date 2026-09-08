import argparse
import json
from pathlib import Path
import pandas
from triplets.tools import get_namespace_map
from triplets.rdfs_tools import rdfs_tools
from triplets.rdfs_tools.rdfs_tools import load_all_to_dataframe
import logging

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parents[2]
RDFS_ROOT = REPO_ROOT / "rdfs"
EXPORT_DIR = REPO_ROOT / "triplets" / "export_schema"

cgmes_data_types_map = {
 'String': 'xsd:string',
 'Simple_Float': 'xsd:float',
 'Float': 'xsd:float',
 'Boolean': 'xsd:boolean',
 'Reactance': 'xsd:float',
 'Resistance': 'xsd:float',
 'Voltage': 'xsd:float',
 'Integer': 'xsd:integer',
 'ActivePower': 'xsd:float',
 'ReactivePower': 'xsd:float',
 'CurrentFlow': 'xsd:float',
 'AngleDegrees': 'xsd:float',
 'PerCent': 'xsd:float',
 'Conductance': 'xsd:float',
 'Susceptance': 'xsd:float',
 'PU': 'xsd:float',
 'Date': 'xsd:date',
 'Length': 'xsd:float',
 'DateTime': 'xsd:dateTime',
 'ApparentPower': 'xsd:float',
 'Seconds': 'xsd:float',
 'Inductance': 'xsd:float',
 'Money': 'xsd:float',
 'MonthDay': 'xsd:integer',
 'VoltagePerReactivePower': 'xsd:float',
 'Capacitance': 'xsd:float',
 'ActivePowerPerFrequency': 'xsd:float',
 'ResistancePerLength': 'xsd:float',
 'RotationSpeed': 'xsd:float',
 'AngleRadians': 'xsd:float',
 'InductancePerLength': 'xsd:float',
 'ActivePowerPerCurrentFlow': 'xsd:float',
 'CapacitancePerLength': 'xsd:float',
 'Decimal': 'xsd:float',
 'Frequency': 'xsd:float',
 'Temperature': 'xsd:float',
 "IRI": "xsd:anyURI",
 "URI": "xsd:anyURI"
}

cim_serializations = {
"552_ED1": {
    "conformsTo":"urn:iso:std:iec:61970-552:2013",
    "id_attribute": "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}ID",
    "id_prefix": "_",
    "about_attribute": "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about",
    "about_prefix": "#_",
    "resource_attribute": "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}resource",
    "resource_prefix": "#_",
    "enumeration_attribute": "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}resource",
    "enumeration_prefix": "",
    },
"552_ED2": {
    "conformsTo":"urn:iso:std:iec:61970-552:2016",
    "id_attribute": "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about",
    "id_prefix": "urn:uuid:",
    "about_attribute": "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about",
    "about_prefix": "urn:uuid:",
    "resource_attribute": "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}resource",
    "resource_prefix": "urn:uuid:",
    "enumeration_attribute": "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}resource",
    "enumeration_prefix": "",
    }
}

def convert_profile(profile_data, serialization_version="552_ED2"):

    id_attribute = cim_serializations[serialization_version]["id_attribute"]
    id_prefix = cim_serializations[serialization_version]["id_prefix"]

    about_attribute = cim_serializations[serialization_version]["about_attribute"]
    about_prefix = cim_serializations[serialization_version]["about_prefix"]

    resource_attribute = cim_serializations[serialization_version]["resource_attribute"]
    resource_prefix = cim_serializations[serialization_version]["resource_prefix"]

    enumeration_attribute = cim_serializations[serialization_version]["enumeration_attribute"]
    enumeration_prefix = cim_serializations[serialization_version]["enumeration_prefix"]

    # Get namspace map
    namespace_map, xml_base = get_namespace_map(profile_data)

    # Dictionary to keep current profile schema
    profile = {}
    profile["ProfileNamespaceMap"] = namespace_map
    profile["ProfileXMLBase"] = xml_base

    classes_defined_externally = profile_data.query(rdfs_tools.stereotype_query("Description")).ID.to_list()

    # Concrete classes are instantiated by ID; Description classes are defined in another
    # profile and referenced by about (e.g. NC associations attached to EQ objects)
    export_classes = list(dict.fromkeys(rdfs_tools.concrete_classes_list(profile_data) + classes_defined_externally))

    def add_parameter(parameter, parameter_meta):
        """Emit one attribute's definition as a top-level profile entry (datatype,
        multiplicity, enum values, ...). Returns its name, or None for an unused
        association (skipped). Does not attach it to a class — the caller does that."""

        # Pivot yields NaN for keys a parameter does not have, semantics expect them absent
        parameter_dict = {key: value for key, value in parameter_meta.to_dict().items() if pandas.notna(value)}

        # TODO - export this and add it to Association metadata
        association_used = parameter_dict.get("AssociationUsed")

        # If it is association but not used, we don't export it
        if association_used == 'No':
            return None

        parameter_namespace, parameter_name = rdfs_tools.get_namespace_and_name(parameter, default_namespace=xml_base)

        parameter_def = {
            "description": parameter_dict.get("comment", ""),
            "multiplicity": parameter_dict["multiplicity"].split("M:")[1],
            "namespace": parameter_namespace
        }

        parameter_def["xsd:minOccours"], parameter_def["xsd:maxOccours"] = rdfs_tools.parse_multiplicity(parameter_dict["multiplicity"])

        # If association
        if association_used == 'Yes':
            parameter_def["attrib"] = {
                "attribute": resource_attribute,
                "value_prefix": resource_prefix
            }

            parameter_def["type"] = "Association"
            parameter_def["xsd:type"] = "xsd:anyURI"
            parameter_def["range"] = parameter_dict["range"]

        else:
            data_type = parameter_dict.get("dataType")

            # If regular attribute, find its data type and add to export
            if data_type:

                # Set parameter type to Attribute
                parameter_def["type"] = "Attribute"

                # Get the attribute data type and add to export
                data_type_namespace, data_type_name = rdfs_tools.get_namespace_and_name(data_type, default_namespace=xml_base)

                data_type_meta = profile_data.get_object_data(data_type).to_dict()

                if data_type_namespace == "":
                    data_type_namespace = xml_base

                data_type_def = {
                    "description": data_type_meta.get("comment", ""),
                    "type": data_type_meta.get("stereotype", ""),
                    "xsd:type": cgmes_data_types_map.get(data_type_name, ""),
                    "namespace": data_type_namespace
                }

                # Add data type to export
                profile[data_type_name] = data_type_def

                # Add data type to attribute definition
                parameter_def["dataType"] = data_type_name
                parameter_def["xsd:type"] = data_type_def["xsd:type"]

            # If enumeration
            else:
                parameter_def["attrib"] = {
                    "attribute": enumeration_attribute,
                    "value_prefix": enumeration_prefix  # TODO - prefix should be used per value
                }
                parameter_def["type"] = "Enumeration"
                parameter_def["xsd:type"] = "xsd:anyURI"
                parameter_def["range"] = parameter_dict["range"].replace("#", "")
                parameter_def["values"] = []

                # Add allowed values
                values = profile_data.query(f"VALUE == '{parameter_dict['range']}' and KEY == 'type'").ID.tolist()

                for value in values:

                    value_namespace, value_name = rdfs_tools.get_namespace_and_name(value, default_namespace=xml_base)
                    value_meta = profile_data.get_object_data(value).to_dict()

                    if value_namespace == "":
                        value_namespace = xml_base

                    value_def = {
                        "description": value_meta.get("comment", ""),
                        "namespace": value_namespace,
                        "type": "EnumerationValue"
                    }

                    parameter_def["values"].append(value_name)
                    profile[value_name] = value_def

        # Add parameter definition
        profile[parameter_name] = parameter_def
        return parameter_name

    for concrete_class in export_classes:

        # Define class namespace
        class_namespace, class_name = rdfs_tools.get_namespace_and_name(concrete_class, default_namespace=xml_base)

        class_meta = profile_data.get_object_data(concrete_class).to_dict()

        # ----------------------------------------------------------------------
        # 1. Decide *how* the class is identified in the XML output
        # ----------------------------------------------------------------------
        #   * If the class lives **inside** this profile → use the normal RDF-ID
        #   * If the class is **imported** from another profile (EQ, TP, …) → use
        #     the “about” attribute (the class already has a global URI)
        # ----------------------------------------------------------------------
        class_is_local = concrete_class not in classes_defined_externally
        class_ID_attribute = id_attribute if class_is_local else about_attribute
        class_ID_prefix = id_prefix if class_is_local else about_prefix

        class_parameters_table, class_inheritance = rdfs_tools.parameters_tableview_all(profile_data, concrete_class)


        # Add class definition
        profile[class_name] = {
            "attrib": {
                "attribute": class_ID_attribute,
                "value_prefix": class_ID_prefix
            },
            "type": "Class",
            "inheritance": class_inheritance,
            "stereotyped": not class_is_local,
            "namespace": class_namespace,
            "description": class_meta.get("comment", ""),
            "parameters": []
        }

        # Add attributes
        for parameter, parameter_meta in class_parameters_table.iterrows():
            parameter_name = add_parameter(parameter, parameter_meta)
            if parameter_name is not None:
                profile[class_name]["parameters"].append(parameter_name)

    # Orphaned attributes: rdf:Property records with no class binding at all (no
    # rdfs:domain and no schema:domainIncludes). Their definition is still emitted
    # as a top-level entry so it is not lost — it is simply not referenced by any
    # class. A consumer can recover the unreferenced set by diffing the property
    # entries against the classes' parameter lists. A property that carries a
    # binding but is skipped for other reasons (e.g. an unused inverse association)
    # is bound, not orphaned.
    bound_ids = set(profile_data.query("KEY in ['domain', 'domainIncludes']")["ID"])
    all_property_ids = profile_data.query(
        "KEY == 'type' and VALUE == 'http://www.w3.org/1999/02/22-rdf-syntax-ns#Property'")["ID"].unique()
    orphan_ids = [pid for pid in all_property_ids if pid not in bound_ids]
    if orphan_ids:
        logger.warning("%d attribute(s) have no class binding (no rdfs:domain / schema:domainIncludes) "
                       "— emitted without a class: %s", len(orphan_ids), ", ".join(sorted(orphan_ids)))
        orphan_rows = profile_data[profile_data["ID"].isin(orphan_ids)].drop_duplicates(["ID", "KEY"])
        orphan_table = orphan_rows.pivot(index="ID", columns="KEY")["VALUE"]
        for parameter, parameter_meta in orphan_table.iterrows():
            add_parameter(parameter, parameter_meta)

    return profile

def convert(data, serialization_version="552_ED2"):

   # Dictionary to keep all configurations
    #conf_dict = {}
    conf_list = []

    # For each profile in loaded RDFS
    profiles = data["INSTANCE_ID"].unique()

    for profile in profiles:
        profile_data = data.query(f"INSTANCE_ID == '{profile}'")

        # Get current profile metadata
        metadata = get_metadata(profile_data).to_dict()
        metadata["serialization"] = serialization_version
        #profile_name = metadata["keyword"]

        profile = {"ProfileMetadata": metadata}

        profile.update(convert_profile(profile_data, serialization_version))

        #conf_dict[profile_name] = profile
        conf_list.append(profile)

    return conf_list

def insert_profile_into_profile(insert_to, insert_what, subset=None):

    insert_to = insert_to.copy()

    insert_to.update(insert_what.get(subset, insert_what))

    return insert_to




def get_metadata(data):

    # OWL metadata
    metadata = rdfs_tools.get_owl_metadata(data)

    # Get some data from category
    category = data.merge(data.query("VALUE == 'http://iec.ch/TC57/1999/rdf-schema-extensions-19990926#ClassCategory'")["ID"])
    category = category[category.ID.str.contains("Profile")]
    category_metadata = category.query("KEY == 'label' or KEY == 'comment'")[["KEY", "VALUE"]].set_index("KEY")["VALUE"]


    if metadata.empty:
        # Make Older CGMES 2.4 ENTSO-E CIM RDFS metadata compatible with new owl based metadata
        metadata = rdfs_tools.get_profile_metadata(data)

        if not metadata.empty:

            metadata["publisher"] = "ENTSO-E"
            metadata["title"] = metadata["shortName"]
            metadata["keyword"] = metadata["shortName"]
            metadata["versionInfo"] = uml.split("v")[-1] if (uml := metadata.get("entsoeUML")) else ""
            metadata["modified"] = metadata["date"]

    metadata = pandas.concat([metadata, category_metadata])
    metadata["title"] = data.type_tableview("Distribution").label.iloc[0].rsplit("/",1)[-1]

    return metadata


def export_single_profile(path, serialization_version="552_ED2", additional_metadata=None):

    data = load_all_to_dataframe(path)

    metadata = get_metadata(data).to_dict()

    if additional_metadata:
        metadata.update(additional_metadata)

    conf_dict = convert(data, serialization_version)

    metadata["serialization_version"] = serialization_version

    file_name = "../export_schema/{publisher}_{keyword}_{versionInfo}_{modified}_{serialization_version}.json".format(**metadata)

    with open(file_name, "w") as file_object:
        json.dump(conf_dict, file_object, indent=4)

    return conf_dict

def index_by_keyword(merged_profiles):
    """Index profiles by their keyword; profiles without one are dropped with an error."""
    indexed = {}
    for profile in merged_profiles:
        if "keyword" in profile["ProfileMetadata"]:
            indexed[profile["ProfileMetadata"]["keyword"]] = profile
        else:
            logger.error(f"Missing keyword in profile: {profile['ProfileMetadata']}, will not be included in export")

    return indexed


def index_largest_per_keyword(merged_profiles):
    """Index profiles by keyword (underscores stripped), keeping the largest profile per keyword."""
    loaded_meta = pandas.DataFrame(
        [{"profileSize": len(meta), **meta["ProfileMetadata"]} for meta in merged_profiles])

    largest = loaded_meta.groupby("keyword")["profileSize"].idxmax().to_list()

    return {profile["ProfileMetadata"]["keyword"].replace("_", ""): profile
            for index, profile in enumerate(merged_profiles) if index in largest}


BUNDLES = {
    "ENTSOE_CGMES_2.4.15": dict(rdfs_dir="ENTSOE_CGMES_2.4.15",
                                header="ENTSOE_FH/Header-AP-Voc-RDFS2020_v2-3-5.rdf",
                                index=index_largest_per_keyword),
    "ENTSOE_CGMES_3.0.0":  dict(rdfs_dir="ENTSOE_CGMES_3.0.0",
                                header="ENTSOE_FH/Header-AP-Voc-RDFS2020_v2-3-5.rdf",
                                index=index_by_keyword),
    "ENTSOE_NC_2.4.1":     dict(rdfs_dir="ENTSOE_NC_2.4.1",
                                header="ENTSOE_NC_2.4.1/DatasetMetadata-AP-Voc-RDFS2020.rdf",
                                exclude={"DatasetMetadata-AP-Voc-RDFS2020.rdf"},
                                index=index_by_keyword),
    # Draft on main (no ncp-v2-5-0 branch yet). DatasetMetadata class linkage restored
    # via schema:domainIncludes in application-profiles-library#99 (fixes #92).
    "ENTSOE_NC_2.5-dev":   dict(rdfs_dir="ENTSOE_NC_2.5-dev",
                                header="ENTSOE_NC_2.5-dev/DatasetMetadata-AP-Voc-RDFS2020.rdf",
                                exclude={"DatasetMetadata-AP-Voc-RDFS2020.rdf"},
                                index=index_by_keyword),
}


def build_bundle(name, spec, serialization_versions=("552_ED1", "552_ED2")):

    # Load Header
    header_data = load_all_to_dataframe(str(RDFS_ROOT / spec["header"]))
    header_profile = convert_profile(header_data, serialization_version="552_ED2")
    header_profile.pop("ProfileXMLBase")
    header_namespace_map = header_profile.pop("ProfileNamespaceMap")

    # Identity of the header profile injected into each section
    header_metadata = {key: value for key, value in get_metadata(header_data).to_dict().items()
                       if key in ("keyword", "title", "identifier", "versionInfo", "versionIRI")}

    # Load Schema
    files_list = [file for file in rdfs_tools.list_of_files(str(RDFS_ROOT / spec["rdfs_dir"]), ".rdf")
                  if Path(file).name not in spec.get("exclude", set())]
    data = load_all_to_dataframe(files_list)

    for serialization_version in serialization_versions:

        merged_profiles = convert(data, serialization_version)
        indexed_profiles = spec["index"](merged_profiles)

        for keyword, profile in indexed_profiles.items():
            # Insert Header entries into each profile, only if missing
            profile.update({key: value for key, value in header_profile.items() if key not in profile})

            # Add header namespaces to map, only if missing
            profile["ProfileNamespaceMap"].update(
                {key: value for key, value in header_namespace_map.items() if
                 key not in profile["ProfileNamespaceMap"]})

            profile["ProfileMetadata"]["header"] = header_metadata

        export_file_name = EXPORT_DIR / f"{name}_{serialization_version}.json"

        with open(export_file_name, "w") as file_object:
            json.dump(indexed_profiles, file_object, indent=4)

        logger.info(f"Exported to {export_file_name}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate export schema JSON bundles from RDFS")
    parser.add_argument("bundles", nargs="*", choices=[[], *BUNDLES], default=[],
                        help="bundle names to generate (default: all)")
    args = parser.parse_args(argv)

    for name in args.bundles or BUNDLES:
        build_bundle(name, BUNDLES[name])


if __name__ == '__main__':

    import sys
    logging.basicConfig(stream=sys.stdout,
                        format='%(levelname) -10s %(asctime)s %(name) -30s %(funcName) -35s %(lineno) -5d: %(message)s',
                        level=logging.INFO)
    main()








