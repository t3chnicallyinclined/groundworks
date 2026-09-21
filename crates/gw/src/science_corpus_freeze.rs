use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

pub fn freeze(
    collection: &str,
    source: &Path,
    output: &Path,
    captured_at: &str,
) -> Result<(), String> {
    if collection.is_empty() || captured_at.is_empty() {
        return Err("collection and capture time are required".into());
    }
    if !source.is_dir() {
        return Err("source root does not exist or is not a directory".into());
    }
    let root = fs::canonicalize(source).map_err(|e| e.to_string())?;
    let out_abs = if output.exists() {
        fs::canonicalize(output).map_err(|e| e.to_string())?
    } else {
        output
            .parent()
            .unwrap_or(Path::new("."))
            .canonicalize()
            .map_err(|e| e.to_string())?
            .join(output.file_name().ok_or("invalid output path")?)
    };
    if out_abs.starts_with(&root) {
        return Err("output path must be outside source root".into());
    }
    let mut files = Vec::new();
    walk(&root, &root, &mut files)?;
    files.sort_by(|a, b| a.0.cmp(&b.0));
    let mut seen: BTreeMap<String, String> = BTreeMap::new();
    let mut entries = Vec::new();
    for (path, bytes) in files {
        let hash = format!("{:x}", Sha256::digest(&bytes));
        let alias_of = seen.get(&hash).cloned();
        if alias_of.is_none() {
            seen.insert(hash.clone(), path.clone());
        }
        let media = media_type(&path);
        let mut e =
            serde_json::json!({"path":path,"bytes":bytes.len(),"sha256":hash,"media_type":media});
        if let Some(a) = alias_of {
            e["alias_of"] = serde_json::Value::String(a);
        }
        entries.push(e);
    }
    let manifest = serde_json::json!({"schema":"gw.science.corpus","version":1,"collection_id":collection,"captured_at":captured_at,"originals":entries});
    if let Some(parent) = output.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let data = serde_json::to_string_pretty(&manifest).map_err(|e| e.to_string())? + "\n";
    fs::write(output, data).map_err(|e| e.to_string())
}
fn walk(root: &Path, dir: &Path, out: &mut Vec<(String, Vec<u8>)>) -> Result<(), String> {
    let mut items: Vec<_> = fs::read_dir(dir)
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    items.sort_by_key(|e| e.file_name());
    for item in items {
        let p = item.path();
        let md = fs::symlink_metadata(&p).map_err(|e| e.to_string())?;
        if md.file_type().is_symlink() {
            let target = fs::canonicalize(&p).map_err(|e| e.to_string())?;
            if !target.starts_with(root) {
                return Err("source contains a symlink outside root".into());
            }
        }
        if md.is_dir() {
            walk(root, &p, out)?;
        } else if md.is_file() {
            let rel = p
                .strip_prefix(root)
                .unwrap()
                .to_string_lossy()
                .replace('\\', "/");
            out.push((rel, fs::read(&p).map_err(|e| e.to_string())?));
        }
    }
    Ok(())
}
fn media_type(path: &str) -> &'static str {
    match Path::new(path)
        .extension()
        .and_then(|x| x.to_str())
        .unwrap_or("")
        .to_ascii_lowercase()
        .as_str()
    {
        "pdf" => "application/pdf",
        "mp4" => "video/mp4",
        "mov" => "video/quicktime",
        "txt" => "text/plain",
        _ => "application/octet-stream",
    }
}
