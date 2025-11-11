import json
from triplets.rdfs_tools import *
import logging

logger = logging.getLogger(__name__)

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

    classes_defined_externally = profile_data.query("KEY == 'stereotype' and VALUE == 'Description'").ID.to_list()

    # Add concrete classes
    for concrete_class in concrete_classes_list(profile_data):

        # Define class namespace
        class_namespace, class_name = get_namespace_and_name(concrete_class, default_namespace=xml_base)

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

        class_parameters_table, class_inheritance = parameters_tableview_all(profile_data, concrete_class)


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

            parameter_dict = parameter_meta.to_dict()

            # TODO - export this and add it to Association metadata
            association_used = parameter_dict.get("AssociationUsed")

            # If it is association but not used, we don't export it
            if association_used == 'No':
                continue

            parameter_namespace, parameter_name = get_namespace_and_name(parameter, default_namespace=xml_base)

            parameter_def = {
                "description": parameter_dict.get("comment", ""),
                "multiplicity": parameter_dict["multiplicity"].split("#M:")[1],
                "namespace": parameter_namespace
            }

            parameter_def["xsd:minOccours"], parameter_def["xsd:maxOccours"] = parse_multiplicity(parameter_dict["multiplicity"])

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
                    data_type_namespace, data_type_name = get_namespace_and_name(data_type, default_namespace=xml_base)

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

                        value_namespace, value_name = get_namespace_and_name(value, default_namespace=xml_base)
                        value_meta = data.get_object_data(value).to_dict()

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

            # Add to class
            profile[class_name]["parameters"].append(parameter_name)

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
    metadata = get_owl_metadata(data)

    # Get some data from category
    category = data.merge(data.query("VALUE == 'http://iec.ch/TC57/1999/rdf-schema-extensions-19990926#ClassCategory'")["ID"])
    category = category[category.ID.str.contains("Profile")]
    category_metadata = category.query("KEY == 'label' or KEY == 'comment'")[["KEY", "VALUE"]].set_index("KEY")["VALUE"]


    if metadata.empty:
        # Make Older CGMES 2.4 ENTSO-E CIM RDFS metadata compatible with new owl based metadata
        metadata = get_profile_metadata(data)

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

    file_name = "../export_schema/{publisher}_{label}_{versionInfo}_{modified}_{serialization_version}.json".format(**metadata)

    with open(file_name, "w") as file_object:
        json.dump(conf_dict, file_object, indent=4)

    return conf_dict

def convert_entsoe_cgmes_2_4():
    base_name = "ENTSOE_CGMES_2.4.15"
    header_name = "Header-AP-Voc-RDFS2020_v2-3-5.rdf"
    serialization_versions = ["552_ED1", "552_ED2"]

    # Load Header
    header_data = load_all_to_dataframe(rf"../../rdfs/ENTSOE_FH/{header_name}")
    header_profile = convert_profile(header_data, serialization_version="552_ED2")
    header_profile.pop("ProfileXMLBase")
    header_namespace_map = header_profile.pop("ProfileNamespaceMap")

    # Load Schema
    files_list = list_of_files(rf"../../rdfs/{base_name}", ".rdf")
    data = load_all_to_dataframe(files_list)

    for serialization_version in serialization_versions:

        merged_profiles = convert(data, serialization_version)

        # Loaded Profiles Meta
        loaded_meta = pandas.DataFrame(
            [{"profileSize": len(meta), **meta["ProfileMetadata"]} for meta in merged_profiles])

        # DataFrame with all largest profiles per keyword
        filtered_meta = data.loc[loaded_meta.groupby("keyword")['profileSize'].idxmax()]

        merged_profiles_filtered = {profile["ProfileMetadata"]["keyword"].replace("_", ""): profile for
                                    index, profile in enumerate(merged_profiles) if
                                    index in filtered_meta.index.to_list()}

        for keyword in merged_profiles_filtered.keys():
            # Insert Header to each profile
            merged_profiles_filtered[keyword].update(header_profile)

            # Add header namespaces to map, only if missing
            merged_profiles_filtered[keyword]["ProfileNamespaceMap"].update(
                {key: value for key, value in header_namespace_map.items() if
                 key not in merged_profiles_filtered[keyword]["ProfileNamespaceMap"]})

        export_file_name = f"../export_schema/{base_name}_{serialization_version}.json"

        with open(export_file_name, "w") as file_object:
            json.dump(merged_profiles_filtered, file_object, indent=4)

        logger.info(f"Exported to {export_file_name}")


def convert_entsoe_cgmes_3_0():
    base_name = "ENTSOE_CGMES_3.0.0"
    header_name = "Header-AP-Voc-RDFS2020_v2-3-5.rdf"
    serialization_versions = ["552_ED1", "552_ED2"]

    # Load Header
    header_data = load_all_to_dataframe(rf"../../rdfs/ENTSOE_FH/{header_name}")
    header_profile = convert_profile(header_data, serialization_version="552_ED2")
    header_profile.pop("ProfileXMLBase")
    header_namespace_map = header_profile.pop("ProfileNamespaceMap")

    # Load Schema
    files_list = list_of_files(rf"../../rdfs/{base_name}", ".rdf")
    data = load_all_to_dataframe(files_list)

    for serialization_version in serialization_versions:

        merged_profiles = convert(data, serialization_version)

        # Loaded Profiles Meta
        loaded_meta = pandas.DataFrame([{"profileSize": len(meta), **meta["ProfileMetadata"]} for meta in merged_profiles])

        merged_profiles_filtered = {}
        for profile in merged_profiles:
            if "keyword" in profile["ProfileMetadata"]:
                merged_profiles_filtered[profile["ProfileMetadata"]["keyword"]] = profile
            else:
                logger.error(f"Missing keyword in profile: {profile['ProfileMetadata']}, will not be included in export")

        for keyword in merged_profiles_filtered.keys():
            # Insert Header to each profile
            merged_profiles_filtered[keyword].update(header_profile)

            # Add header namespaces to map, only if missing
            merged_profiles_filtered[keyword]["ProfileNamespaceMap"].update({key: value for key, value in header_namespace_map.items() if key not in merged_profiles_filtered[keyword]["ProfileNamespaceMap"]})

        export_file_name = f"../export_schema/{base_name}_{serialization_version}.json"

        with open(export_file_name, "w") as file_object:
            json.dump(merged_profiles_filtered, file_object, indent=4)

        logger.info(f"Exported to {export_file_name}")


def convert_entsoe_nc():
    base_name = "ENTSOE_NC"
    header_name = "DatasetMetadata-AP-Voc-RDFS2020_v3-0-0.rdf"
    serialization_versions = ["552_ED1", "552_ED2"]

    # Load Header
    header_data = load_all_to_dataframe(rf"../../rdfs/ENTSOE_FH/{header_name}")
    header_profile = convert_profile(header_data, serialization_version="552_ED2")
    header_profile.pop("ProfileXMLBase")
    header_namespace_map = header_profile.pop("ProfileNamespaceMap")

    # Load Schema
    files_list = list_of_files(rf"../../rdfs/{base_name}", ".rdf")
    data = load_all_to_dataframe(files_list)

    for serialization_version in serialization_versions:

        merged_profiles = convert(data, serialization_version)

        # Loaded Profiles Meta
        loaded_meta = pandas.DataFrame(
            [{"profileSize": len(meta), **meta["ProfileMetadata"]} for meta in merged_profiles])

        merged_profiles_filtered = {}
        for profile in merged_profiles:
            if "keyword" in profile["ProfileMetadata"]:
                merged_profiles_filtered[profile["ProfileMetadata"]["keyword"]] = profile
            else:
                logger.error(
                    f"Missing keyword in profile: {profile['ProfileMetadata']}, will not be included in export")

        for keyword in merged_profiles_filtered.keys():
            # Insert Header to each profile
            merged_profiles_filtered[keyword].update(header_profile)

            # Add header namespaces to map, only if missing
            merged_profiles_filtered[keyword]["ProfileNamespaceMap"].update(
                {key: value for key, value in header_namespace_map.items() if
                 key not in merged_profiles_filtered[keyword]["ProfileNamespaceMap"]})

        export_file_name = f"../export_schema/{base_name}_{serialization_version}.json"

        with open(export_file_name, "w") as file_object:
            json.dump(merged_profiles_filtered, file_object, indent=4)

        logger.info(f"Exported to {export_file_name}")


if __name__ == '__main__':

    import sys
    logging.basicConfig(stream=sys.stdout,
                        format='%(levelname) -10s %(asctime)s %(name) -30s %(funcName) -35s %(lineno) -5d: %(message)s',
                        level=logging.DEBUG)


    path = r"../../rdfs/ENTSOE_CGMES_2.4.15/EquipmentProfileCoreRDFSAugmented-v2_4_15-4Sep2020.rdf"
    #path = r"/rdfs/ENTSOE_FH/DatasetMetadata-AP-Voc-RDFS2020_v3-0-0.rdf"
    #path = r"/rdfs/ENTSOE_FH/Header-AP-Voc-RDFS2020_v2-3-5.rdf"


    serialization_version = "552_ED2"

    export = export_single_profile(path, serialization_version)

    # path = r"../../rdfs/ENTSOE_CGMES_2.4.15/EquipmentProfileCoreRDFSAugmented-v2_4_15-4Sep2020.rdf"
    # path = r"../../rdfs/ENTSOE_CGMES_2.4.15/FileHeader.rdf"
    # path = r"../../rdfs/ENTSOE_CGMES_3.0.0/FileHeader_RDFS2019.rdf"
    # path = r"../../rdfs/ENTSOE_CGMES_2.4.15/EquipmentProfileCoreRDFSAugmented-v2_4_15-4Sep2020.rdf"


    # path_old = r"../../rdfs/ENTSOE_CGMES_2.4.15/FileHeader.rdf"
    #
    # data_new = load_all_to_dataframe(path_new)
    # print(data.merge(data.query("KEY == 'type' and VALUE == 'http://www.w3.org/2002/07/owl#Ontology'").ID))
    #
    # data_old = load_all_to_dataframe(path_old)
    # profile_domain = data_old.query("VALUE == 'baseUML'")["ID"].to_list()[0].split(".")[0]
    # print(data_old[data_old.ID.str.contains(profile_domain)].query("KEY == 'isFixed'"))








