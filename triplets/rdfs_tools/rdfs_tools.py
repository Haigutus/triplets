from triplets.rdf_parser import load_all_to_dataframe
import pandas
import os

import logging

logger = logging.getLogger(__name__)

pandas.set_option("display.max_rows", 20)
pandas.set_option("display.max_columns", 8)
pandas.set_option("display.width", 1000)
pandas.set_option('display.max_colwidth', None)


def get_owl_metadata(data):
    """Returns metadata about Profile defined in RDFS OWL Ontology"""
    return data.merge(data.query("KEY == 'type' and VALUE == 'http://www.w3.org/2002/07/owl#Ontology'").ID).set_index("KEY")["VALUE"]

def get_profile_metadata(data):
    """Returns metadata about CIM profile defined in RDFS"""

    profile_domain = data.query("VALUE == 'baseUML'")["ID"].to_list()[0].split(".")[0]
    profile_metadata = data[data.ID.str.contains(profile_domain)].query("KEY == 'isFixed'").copy(deep=True)

    profile_metadata["ID"] = profile_metadata.ID.str.split("#", expand=True)[1].str.split(".", expand=True)[1]

    return profile_metadata.set_index("ID")["VALUE"]


def list_of_files(root_path, file_extension, deep=False):

    matches = []
    root_path = os.path.abspath(root_path)
    ext = file_extension.lower()

    if not os.path.exists(root_path):
        print(f"Path does not exist: {root_path}")
        return []

    if os.path.isfile(root_path):
        return [root_path] if root_path.lower().endswith(ext) else []

    # Directory case
    if deep:
        for dirpath, _, filenames in os.walk(root_path):
            for filename in filenames:
                if filename.lower().endswith(ext):
                    matches.append(os.path.join(dirpath, filename))
    else:
        for filename in os.listdir(root_path):
            full_path = os.path.join(root_path, filename)
            if os.path.isfile(full_path) and filename.lower().endswith(ext):
                matches.append(full_path)

    return matches



def get_class_parameters(data, class_name):
    """Returns parameters of the class and all the class names it extends"""

    class_data = {"name": class_name}

    # Get parameters
    class_data["parameters"] = data.query("VALUE == @class_name & KEY == 'domain'")

    # Add parent classes (if present)
    class_data["extends"] = list(data.query("ID == @class_name and KEY == 'subClassOf'")["VALUE"].unique())

    # Usually only one inheritance, warn if not
    if len(class_data["extends"]) > 1:
        logger.warning(f"{class_name} is inheriting form more than one class -> {class_data['extends']}")

    return class_data


def get_all_class_parameters(data, class_name):
    """Returns all parameters of the class including from classes it extends"""

    all_class_parameters = pandas.DataFrame()
    class_name_list = [class_name]

    for class_name in class_name_list:

        # Get current class parameters
        class_data = get_class_parameters(data, class_name)

        # Add parameters to others
        all_class_parameters = pandas.concat([all_class_parameters, class_data["parameters"]])

        # Add classes that this class extends to processing
        class_name_list.extend(class_data["extends"])

    logger.info("Inheritance sequence")  # TODO add this as a output
    logger.info(" -> ".join(class_name_list))

    return all_class_parameters, class_name_list


def parameters_tableview_all(data, class_name):
    """Provide class name to get table of all class parameters"""

    # Get All parameter names of class (natural and inherited)
    all_class_parameters, inheritance = get_all_class_parameters(data, class_name)

    # Get parameters data
    type_data = all_class_parameters[["ID"]].merge(data, on="ID").drop_duplicates(["ID", "KEY"])

    # Pivot to table
    data_view = type_data.pivot(index="ID", columns="KEY")["VALUE"]

    return data_view, inheritance


def parameters_tableview(data, class_name):
    """Provide class name to get table of class parameters and names of classes it extends"""

    # Get All parameter names of class (natural and inherited)
    class_data = get_class_parameters(data, class_name)

    #print(class_data)

    if not class_data["parameters"].empty:
        # Get parameters data
        type_data = (class_data["parameters"])[["ID"]].merge(data, on="ID").drop_duplicates(["ID", "KEY"])

        # Pivot to table
        data_view = type_data.pivot(index="ID", columns="KEY")["VALUE"]
    else:
        data_view = None

    return data_view, class_data["extends"]


def validation_view(data, class_name):


    data_view, _ = parameters_tableview_all(data, class_name)

    validation_data = multiplicity_to_XSD_format(data_view)

    if "AssociationUsed" in validation_data.columns:
        validation_data = validation_data.query("AssociationUsed != 'No'")

    return validation_data[['minOccurs', 'maxOccurs', 'comment']] #[['minOccurs', 'maxOccurs', 'dataType', 'domain', 'label', 'comment']]


def multiplicity_to_XSD_format(data_table_view):
    """Converts multiplicity defined in extended RDFS to XSD minOccurs and maxOccurs and adds them to the table"""

    multiplicity = data_table_view.multiplicity.str.split("#M:").str[1]
    data_table_view["minOccurs"] = multiplicity.str[0]
    data_table_view["maxOccurs"] = multiplicity.str[-1].str.replace("n", "unbounded")

    return data_table_view


def get_namespace_and_name(uri, default_namespace):

    separator = "#" if "#" in uri else "/"

    namespace, name = uri.rsplit(separator, maxsplit=1)

    if namespace == "":
        namespace = default_namespace

    namespace = f"{namespace}{separator}"


    return namespace, name



def parse_multiplicity(uri):
    """Converts multiplicity defined in extended RDFS to XSD minOccurs and maxOccurs"""

    multiplicity = str(uri).split("#M:")[1]

    minOccurs = multiplicity[0].replace("n", "unbounded")
    maxOccurs = multiplicity[-1]

    return minOccurs, maxOccurs


def concrete_classes_list(data):
    """Returns list of Concrete classes from Triplet"""
    return list(data.query("VALUE == 'http://iec.ch/TC57/NonStandard/UML#concrete'")["ID"])




# Full Model class is missing in RDFS, thus added here manually # TODO add Supersedes
fullmodel_conf = { "FullModel": {
                        "attrib": {
                            "attribute": "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about",
                            "value_prefix": "urn:uuid:"
                            },
                            "namespace": "http://iec.ch/TC57/61970-552/ModelDescription/1#"},

                    "Model.DependentOn": {
                        "attrib": {
                            "attribute": "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}resource",
                            "value_prefix": "urn:uuid:"
                        },
                        "namespace": "http://iec.ch/TC57/61970-552/ModelDescription/1#"
                    },

                    "Model.Supersedes": {
                        "attrib": {
                            "attribute": "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}resource",
                            "value_prefix": "urn:uuid:"
                        },
                        "namespace": "http://iec.ch/TC57/61970-552/ModelDescription/1#"
                    },

                    "Model.created": {
                        "namespace": "http://iec.ch/TC57/61970-552/ModelDescription/1#"
                    },
                    "Model.description": {
                        "namespace": "http://iec.ch/TC57/61970-552/ModelDescription/1#"
                    },
                    "Model.messageType": {
                        "namespace": "http://entsoe.eu/CIM/Extensions/CGM-BP/2020#"
                    },
                    "Model.modelingAuthoritySet": {
                        "namespace": "http://iec.ch/TC57/61970-552/ModelDescription/1#"
                    },
                    "Model.modelingEntity": {
                        "namespace": "http://entsoe.eu/CIM/Extensions/CGM-BP/2020#"
                    },
                    "Model.processType": {
                        "namespace": "http://entsoe.eu/CIM/Extensions/CGM-BP/2020#"
                    },
                    "Model.profile": {
                        "namespace": "http://iec.ch/TC57/61970-552/ModelDescription/1#"
                    },
                    "Model.scenarioTime": {
                        "namespace": "http://iec.ch/TC57/61970-552/ModelDescription/1#"
                    },
                    "Model.version": {
                        "namespace": "http://iec.ch/TC57/61970-552/ModelDescription/1#"
                    }}

def get_used_relations(data):
    relations = data.query("KEY == 'AssociationUsed' and VALUE == 'Yes'").rename(columns={"ID": "RELATION_NAME"})
    return relations.RELATION_NAME.str.split("#").str[-1]

def dangling_references(data, relation_names):
    references = data.merge(relation_names, left_on="KEY", right_on="RELATION_NAME")
    return data.query("KEY == 'Type'").merge(references, left_on="ID", right_on="VALUE", indicator=True, how="right", suffixes=("_TO", "_FROM"))





if __name__ == '__main__':

    path = r"../rdfs/ENTSOE_CGMES_2.4.15/EquipmentProfileCoreOperationShortCircuitRDFSAugmented-v2_4_15-4Sep2020.rdf"

    data = load_all_to_dataframe([path])

    print(validation_view(data, "#ACLineSegment"))
    print(validation_view(data, "#PowerTransformerEnd"))








#print(validation_view("#ACLineSegment"))
#print(validation_view("#PowerTransformerEnd"))