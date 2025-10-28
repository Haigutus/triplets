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
 'Temperature': 'xsd:float'}

cim_serializations = {
"552_ED1": {
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

def get_namespace_map(data: pandas.DataFrame):
    """Extract namespace map from data."""
    namespace_map = data.merge(data.query("KEY == 'Type' and VALUE == 'NamespaceMap'").ID).set_index("KEY")["VALUE"].to_dict()
    namespace_map.pop("Type", None)
    xml_base = namespace_map.pop("xml_base", None)
    return namespace_map, xml_base


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
    profile["NamespaceMap"] = namespace_map
    profile["XMLBase"] = xml_base

    classes_defined_externally = profile_data.query("KEY == 'stereotype' and VALUE == 'Description'").ID.to_list()

    # Add concrete classes
    for concrete_class in concrete_classes_list(profile_data):

        # Define class namespace
        class_namespace, class_name = concrete_class.split("#")

        class_meta = profile_data.get_object_data(concrete_class).to_dict()

        if class_namespace == "":
            class_namespace = xml_base
        else:
            class_namespace = class_namespace + "#"

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


        # Add class definition
        profile[class_name] = {
            "attrib": {
                "attribute": class_ID_attribute,
                "value_prefix": class_ID_prefix
            },
            "type": "Class",
            "stereotyped": not class_is_local,
            "namespace": class_namespace,
            "description": class_meta.get("comment", ""),
            "parameters": []
        }

        # Add attributes

        for parameter, parameter_meta in parameters_tableview_all(profile_data, concrete_class).iterrows():

            parameter_dict = parameter_meta.to_dict()

            print(parameter_dict)

            # TODO - export this and add it to Association metadata
            association_used = parameter_dict.get("AssociationUsed")

            # If it is association but not used, we don't export it
            if association_used == 'No':
                continue

            # If it is used association or regular parameter, then we need the name and namespace
            parameter_namespace, parameter_name = parameter.split("#")

            if parameter_namespace == "":
                parameter_namespace = xml_base

            else:
                parameter_namespace = parameter_namespace + "#"

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
                    data_type_namespace, data_type_name = data_type.split("#")

                    data_type_meta = data.get_object_data(data_type).to_dict()

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

                        value_namespace, value_name = value.split("#")
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
    conf_dict = {}

    # For each profile in loaded RDFS
    profiles = data["INSTANCE_ID"].unique()

    for profile in profiles:
        profile_data = data.query(f"INSTANCE_ID == '{profile}'")

        # Get current profile metadata
        metadata = get_metadata(profile_data).to_dict()
        profile_name = metadata["keyword"]

        profile = {"ProfileMetadata": metadata}

        profile.update(convert_profile(profile_data, serialization_version))


        conf_dict[profile_name] = profile

    return conf_dict



# Add FullModel definiton

# if add_header:
#     with open("ENTSO-E_Document header vocabulary_2.1.0_2022-07-21.json", "r") as file_object:
#         new_fullmodel_conf = json.load(file_object)["DH"]
#
#     for profile_name in conf_dict:
#         conf_dict[profile_name].update(new_fullmodel_conf)

# Export conf

def get_metadata(data):

    # OWL metadata
    metadata = get_owl_metadata(data)

    # Get some data from category
    category = data.merge(data.query("VALUE == 'http://iec.ch/TC57/1999/rdf-schema-extensions-19990926#ClassCategory'")["ID"])
    category = category[category.ID.str.contains("Profile")]
    category_metadata = category.query("KEY == 'label' or KEY == 'comment'")[["KEY", "VALUE"]].set_index("KEY")["VALUE"]


    if metadata.empty:
        # Older CGMES 2.4 ENTSO-E CIM RDFS metadata
        metadata = get_profile_metadata(data)
        metadata["publisher"] = "ENTSO-E"
        metadata["title"] = metadata["shortName"]
        metadata["keyword"] = metadata["shortName"]
        metadata["versionInfo"] = "2.4.15"
        metadata["modified"] = metadata["date"]

    metadata = pandas.concat([metadata, category_metadata])

    return metadata


if __name__ == '__main__':

    import sys
    logging.basicConfig(stream=sys.stdout,
                        format='%(levelname) -10s %(asctime)s %(name) -30s %(funcName) -35s %(lineno) -5d: %(message)s',
                        level=logging.DEBUG)


    path = r"../../rdfs/ENTSOE_CGMES_2.4.15/EquipmentProfileCoreRDFSAugmented-v2_4_15-4Sep2020.rdf"
    #path = r"/home/kristjan/Downloads/ApplicationProfilesLibrary-release1-1-0/DatasetMetadata/CurrentRelease/RDFS/DatasetMetadata-AP-Voc-RDFS2020_v3-0-0.rdf"
    #path = r"/home/kristjan/Downloads/ApplicationProfilesLibrary-release1-1-0/DatasetMetadata/PastReleases/RDFS/Header-AP-Voc-RDFS2020_v2-3-5.rdf"

    serialization_version = "552_ED2"

    data = load_all_to_dataframe(path)

    metadata = get_metadata(data)

    conf_dict = convert(data, serialization_version)

    metadata["serialization_version"] = serialization_version

    file_name = "../export_schema/{publisher}_{label}_{versionInfo}_{modified}_{serialization_version}.json".format(**metadata.to_dict())
    #file_name = "../export_schema/ENTSO-E_FileHeaderProfile_3.0.0_2019-07-26.json"


    with open(file_name, "w") as file_object:
        json.dump(conf_dict, file_object, indent=4)

    # path = r"../../rdfs/ENTSOE_CGMES_2.4.15/EquipmentProfileCoreRDFSAugmented-v2_4_15-4Sep2020.rdf"
    # path = r"../../rdfs/ENTSOE_CGMES_2.4.15/FileHeader.rdf"
    # path = r"../../rdfs/ENTSOE_CGMES_3.0.1/FileHeader_RDFS2019.rdf"
    # path = r"/home/kristjan/Downloads/ApplicationProfilesLibrary-release1-1-0/CGMES/RDFS/61970-600-2_Header-AP-Voc-RDFS2019_v3-0-0.rdf"
    # path = r"/home/kristjan/Downloads/ApplicationProfilesLibrary-release1-1-0/CGMES/RDFS/61970-600-2_Equipment-AP-Voc-RDFS2020_v3-0-0.rdf"
    # path = r"../../rdfs/ENTSOE_CGMES_2.4.15/EquipmentProfileCoreRDFSAugmented-v2_4_15-4Sep2020.rdf"
    path = r"/home/kristjan/GIT/triplets/rdfs/ENTSOE_CGMES_3.0.1/IEC61970-600-2_CGMES_3_0_0_RDFS2020_EQ.rdf"



    # path_new = r"/home/kristjan/GIT/triplets/rdfs/ENTSOE_CGMES_3.0.1/IEC61970-600-2_CGMES_3_0_0_RDFS2020_SSH.rdf"
    # path_old = r"/home/kristjan/GIT/triplets/rdfs/ENTSOE_CGMES_2.4.15/SteadyStateHypothesisProfileRDFSAugmented-v2_4_15-4Sep2020.rdf"
    # path_old = r"../../rdfs/ENTSOE_CGMES_2.4.15/FileHeader.rdf"
    #
    # data_new = load_all_to_dataframe(path_new)
    # print(data.merge(data.query("KEY == 'type' and VALUE == 'http://www.w3.org/2002/07/owl#Ontology'").ID))
    #
    # data_old = load_all_to_dataframe(path_old)
    # profile_domain = data_old.query("VALUE == 'baseUML'")["ID"].to_list()[0].split(".")[0]
    # print(data_old[data_old.ID.str.contains(profile_domain)].query("KEY == 'isFixed'"))

