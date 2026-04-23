use arrow_array::builder::StringBuilder;
use arrow_array::RecordBatch;
use arrow_schema::{DataType, Field, Schema};
use pyo3::prelude::*;
use pyo3_arrow::PyRecordBatch;
use quick_xml::events::Event;
use quick_xml::reader::Reader;
use rayon::prelude::*;
use std::fs;
use std::io::{Cursor, Read};
use std::sync::Arc;
use uuid::Uuid;
use zip::ZipArchive;

/// Remove CIM ID prefixes: "urn:uuid:", "#_", "_"
fn clean_id(id: &str) -> &str {
    let s = id.strip_prefix("urn:uuid:").unwrap_or(id);
    let s = s.strip_prefix("#_").unwrap_or(s);
    s.strip_prefix('_').unwrap_or(s)
}

/// Extract local name from "prefix:localname" or "{namespace}localname"
fn local_name(tag: &[u8]) -> String {
    let s = std::str::from_utf8(tag).unwrap_or("");
    // Handle "{ns}local" format
    if let Some(pos) = s.rfind('}') {
        return s[pos + 1..].to_string();
    }
    // Handle "prefix:local" format
    if let Some(pos) = s.rfind(':') {
        return s[pos + 1..].to_string();
    }
    s.to_string()
}

/// Get attribute value by local name from quick-xml attributes
fn get_attr<'a>(e: &'a quick_xml::events::BytesStart<'a>, local: &str) -> Option<String> {
    for attr in e.attributes().flatten() {
        let key = std::str::from_utf8(attr.key.as_ref()).unwrap_or("");
        let key_local = if let Some(pos) = key.rfind(':') {
            &key[pos + 1..]
        } else {
            key
        };
        if key_local == local {
            return Some(
                attr.unescape_value()
                    .unwrap_or_default()
                    .into_owned(),
            );
        }
    }
    None
}

/// Parsed RDF data from a single file
struct RdfData {
    ids: Vec<String>,
    keys: Vec<String>,
    values: Vec<Option<String>>,
    instance_ids: Vec<String>,
}

/// Parse a single RDF XML from bytes
fn parse_rdf_bytes(data: &[u8], file_name: &str) -> RdfData {
    let instance_id = Uuid::new_v4().to_string();
    let meta_id = Uuid::new_v4().to_string();
    let nsmap_id = Uuid::new_v4().to_string();

    // Pre-allocate with estimated capacity
    let estimated_rows = data.len() / 80; // rough estimate
    let mut ids = Vec::with_capacity(estimated_rows);
    let mut keys = Vec::with_capacity(estimated_rows);
    let mut values: Vec<Option<String>> = Vec::with_capacity(estimated_rows);
    let mut instance_ids = Vec::with_capacity(estimated_rows);

    // Add metadata rows
    // Distribution
    ids.push(meta_id.clone());
    keys.push("Type".to_string());
    values.push(Some("Distribution".to_string()));
    instance_ids.push(instance_id.clone());

    ids.push(meta_id);
    keys.push("label".to_string());
    values.push(Some(file_name.to_string()));
    instance_ids.push(instance_id.clone());

    // NamespaceMap
    ids.push(nsmap_id.clone());
    keys.push("Type".to_string());
    values.push(Some("NamespaceMap".to_string()));
    instance_ids.push(instance_id.clone());

    let mut reader = Reader::from_reader(Cursor::new(data));
    reader.config_mut().trim_text(true);

    let mut buf = Vec::with_capacity(4096);
    let mut depth: u32 = 0;
    let mut current_object_id = String::new();
    let mut current_property_key = String::new();
    let mut current_text = String::new();
    let mut property_has_value = false;
    let mut ns_map_done = false;

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) => {
                depth += 1;

                if depth == 1 {
                    // rdf:RDF root — extract namespace declarations
                    if !ns_map_done {
                        for attr in e.attributes().flatten() {
                            let key = std::str::from_utf8(attr.key.as_ref()).unwrap_or("");
                            let val = attr.unescape_value().unwrap_or_default().into_owned();

                            if let Some(prefix) = key.strip_prefix("xmlns:") {
                                ids.push(nsmap_id.clone());
                                keys.push(prefix.to_string());
                                values.push(Some(val));
                                instance_ids.push(instance_id.clone());
                            } else if key == "xml:base" {
                                ids.push(nsmap_id.clone());
                                keys.push("xml_base".to_string());
                                values.push(Some(val));
                                instance_ids.push(instance_id.clone());
                            }
                        }
                        // Fallback xml_base from file name
                        let has_base = keys.iter().any(|k| k == "xml_base");
                        if !has_base {
                            ids.push(nsmap_id.clone());
                            keys.push("xml_base".to_string());
                            values.push(Some(file_name.to_string()));
                            instance_ids.push(instance_id.clone());
                        }
                        ns_map_done = true;
                    }
                } else if depth == 2 {
                    // RDF object — extract ID
                    let raw_id = get_attr(e, "ID")
                        .or_else(|| get_attr(e, "about"))
                        .or_else(|| get_attr(e, "nodeID"))
                        .unwrap_or_default();
                    current_object_id = clean_id(&raw_id).to_string();

                    // Type from tag
                    let type_val = local_name(e.name().as_ref());
                    ids.push(current_object_id.clone());
                    keys.push("Type".to_string());
                    values.push(Some(type_val));
                    instance_ids.push(instance_id.clone());
                } else if depth == 3 {
                    // Property element
                    current_property_key = local_name(e.name().as_ref());
                    current_text.clear();
                    property_has_value = false;

                    // Check for rdf:resource or rdf:nodeID attribute
                    let ref_val = get_attr(e, "resource")
                        .or_else(|| get_attr(e, "nodeID"));
                    if let Some(rv) = ref_val {
                        let mut cleaned = clean_id(&rv).to_string();
                        // Enumeration handling
                        if cleaned.starts_with("http") {
                            if let Some(pos) = cleaned.rfind('#') {
                                cleaned = cleaned[pos + 1..].to_string();
                            }
                        }
                        ids.push(current_object_id.clone());
                        keys.push(current_property_key.clone());
                        values.push(Some(cleaned));
                        instance_ids.push(instance_id.clone());
                        property_has_value = true;
                    }
                }
            }
            Ok(Event::Empty(ref e)) => {
                // Self-closing element, only relevant at depth 2 (object) or 3 (property)
                let effective_depth = depth + 1;

                if effective_depth == 2 {
                    // Self-closing RDF object (rare but possible)
                    let raw_id = get_attr(e, "ID")
                        .or_else(|| get_attr(e, "about"))
                        .or_else(|| get_attr(e, "nodeID"))
                        .unwrap_or_default();
                    let obj_id = clean_id(&raw_id).to_string();
                    let type_val = local_name(e.name().as_ref());

                    ids.push(obj_id);
                    keys.push("Type".to_string());
                    values.push(Some(type_val));
                    instance_ids.push(instance_id.clone());
                } else if effective_depth == 3 {
                    // Self-closing property (most common: rdf:resource references)
                    let prop_key = local_name(e.name().as_ref());
                    let ref_val = get_attr(e, "resource")
                        .or_else(|| get_attr(e, "nodeID"));

                    let val = if let Some(rv) = ref_val {
                        let mut cleaned = clean_id(&rv).to_string();
                        if cleaned.starts_with("http") {
                            if let Some(pos) = cleaned.rfind('#') {
                                cleaned = cleaned[pos + 1..].to_string();
                            }
                        }
                        Some(cleaned)
                    } else {
                        None
                    };

                    ids.push(current_object_id.clone());
                    keys.push(prop_key);
                    values.push(val);
                    instance_ids.push(instance_id.clone());
                }
            }
            Ok(Event::Text(ref e)) => {
                if depth == 3 {
                    let text = e.unescape().unwrap_or_default().into_owned();
                    current_text.push_str(&text);
                }
            }
            Ok(Event::End(_)) => {
                if depth == 3 && !property_has_value {
                    let val = if current_text.is_empty() {
                        None
                    } else {
                        Some(current_text.clone())
                    };
                    ids.push(current_object_id.clone());
                    keys.push(current_property_key.clone());
                    values.push(val);
                    instance_ids.push(instance_id.clone());
                }
                depth = depth.saturating_sub(1);
            }
            Ok(Event::Eof) => break,
            Err(e) => {
                eprintln!("XML parse error: {e}");
                break;
            }
            _ => {}
        }
        buf.clear();
    }

    RdfData {
        ids,
        keys,
        values,
        instance_ids,
    }
}

/// Convert RdfData to Arrow RecordBatch
fn rdf_data_to_batch(data: RdfData) -> RecordBatch {
    let len = data.ids.len();
    let mut id_builder = StringBuilder::with_capacity(len, len * 36);
    let mut key_builder = StringBuilder::with_capacity(len, len * 20);
    let mut value_builder = StringBuilder::with_capacity(len, len * 30);
    let mut inst_builder = StringBuilder::with_capacity(len, len * 36);

    for i in 0..len {
        id_builder.append_value(&data.ids[i]);
        key_builder.append_value(&data.keys[i]);
        match &data.values[i] {
            Some(v) => value_builder.append_value(v),
            None => value_builder.append_null(),
        }
        inst_builder.append_value(&data.instance_ids[i]);
    }

    let schema = Arc::new(Schema::new(vec![
        Field::new("ID", DataType::Utf8, false),
        Field::new("KEY", DataType::Utf8, false),
        Field::new("VALUE", DataType::Utf8, true),
        Field::new("INSTANCE_ID", DataType::Utf8, false),
    ]));

    RecordBatch::try_new(
        schema,
        vec![
            Arc::new(id_builder.finish()),
            Arc::new(key_builder.finish()),
            Arc::new(value_builder.finish()),
            Arc::new(inst_builder.finish()),
        ],
    )
    .expect("Failed to build RecordBatch")
}

/// Merge multiple RdfData into one
fn merge_rdf_data(parts: Vec<RdfData>) -> RdfData {
    let total: usize = parts.iter().map(|p| p.ids.len()).sum();
    let mut ids = Vec::with_capacity(total);
    let mut keys = Vec::with_capacity(total);
    let mut values = Vec::with_capacity(total);
    let mut instance_ids = Vec::with_capacity(total);

    for part in parts {
        ids.extend(part.ids);
        keys.extend(part.keys);
        values.extend(part.values);
        instance_ids.extend(part.instance_ids);
    }

    RdfData {
        ids,
        keys,
        values,
        instance_ids,
    }
}

/// Find all XML files from paths (supports .xml, .rdf, .zip including nested zips)
fn find_all_xml(paths: &[String]) -> Vec<(Vec<u8>, String)> {
    let mut xml_files: Vec<(Vec<u8>, String)> = Vec::new();
    let mut zip_data: Vec<(Vec<u8>, String)> = Vec::new();

    for path in paths {
        let lower = path.to_lowercase();
        if lower.ends_with(".xml") || lower.ends_with(".rdf") {
            if let Ok(data) = fs::read(path) {
                xml_files.push((data, path.clone()));
            }
        } else if lower.ends_with(".zip") {
            if let Ok(data) = fs::read(path) {
                zip_data.push((data, path.clone()));
            }
        }
    }

    // Process zips (including nested)
    let mut i = 0;
    while i < zip_data.len() {
        let (data, _zip_name) = &zip_data[i];
        if let Ok(mut archive) = ZipArchive::new(Cursor::new(data.clone())) {
            for j in 0..archive.len() {
                if let Ok(mut file) = archive.by_index(j) {
                    let name = file.name().to_string();
                    let lower = name.to_lowercase();
                    let mut buf = Vec::new();
                    if file.read_to_end(&mut buf).is_ok() {
                        if lower.ends_with(".xml") || lower.ends_with(".rdf") {
                            xml_files.push((buf, name));
                        } else if lower.ends_with(".zip") {
                            zip_data.push((buf, name));
                        }
                    }
                }
            }
        }
        i += 1;
    }

    xml_files
}

/// Load RDF XML files and return Arrow RecordBatch
/// Accepts list of paths to .xml, .rdf, or .zip files
#[pyfunction]
#[pyo3(signature = (paths, parallel=true))]
fn load_rdf_to_arrow(paths: Vec<String>, parallel: bool) -> PyResult<PyRecordBatch> {
    let xml_files = find_all_xml(&paths);

    let rdf_data = if parallel && xml_files.len() > 1 {
        // Release GIL and parse in parallel with rayon
        let parts: Vec<RdfData> = xml_files
            .par_iter()
            .map(|(data, name)| parse_rdf_bytes(data, name))
            .collect();
        merge_rdf_data(parts)
    } else {
        let parts: Vec<RdfData> = xml_files
            .iter()
            .map(|(data, name)| parse_rdf_bytes(data, name))
            .collect();
        merge_rdf_data(parts)
    };

    let batch = rdf_data_to_batch(rdf_data);
    Ok(PyRecordBatch::new(batch))
}

/// Python module
#[pymodule]
fn rdf_parser_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(load_rdf_to_arrow, m)?)?;
    Ok(())
}
