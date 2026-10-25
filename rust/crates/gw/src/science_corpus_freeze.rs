use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Component, Path, PathBuf};

pub fn freeze(
    collection: &str,
    source: &Path,
    output: &Path,
    captured_at: &str,
    includes: &[String],
) -> Result<(), String> {
    if collection.is_empty() || !valid_captured_at(captured_at) {
        return Err(
            "collection and capture time are required and capture time must be RFC3339".into(),
        );
    }
    if !source.is_dir() {
        return Err("source root does not exist or is not a directory".into());
    }
    let root = fs::canonicalize(source).map_err(|e| e.to_string())?;
    let out_abs = resolve_output(output)?;
    if out_abs.starts_with(&root) {
        return Err("output path must be outside source root".into());
    }
    let files = if includes.is_empty() {
        let mut files = Vec::new();
        walk(&root, &root, &mut files)?;
        files.sort_by(|a, b| a.0.cmp(&b.0));
        files
    } else {
        let mut files = Vec::new();
        for requested in includes {
            let rel = validate_include(requested)?;
            let path = root.join(&rel);
            let metadata = fs::symlink_metadata(&path)
                .map_err(|_| format!("included path does not exist: {requested}"))?;
            if metadata.file_type().is_symlink() {
                return Err(format!("included path is a symlink: {requested}"));
            }
            if !metadata.is_file() {
                return Err(format!("included path is not a regular file: {requested}"));
            }
            files.push((rel, fs::read(path).map_err(|e| e.to_string())?));
        }
        files.sort_by(|a, b| a.0.cmp(&b.0));
        files
    };
    let mut seen: BTreeMap<String, String> = BTreeMap::new();
    let mut entries = Vec::new();
    for (path, bytes) in files {
        let hash = format!("{:x}", Sha256::digest(&bytes));
        let alias_of = seen.get(&hash).cloned();
        if alias_of.is_none() {
            seen.insert(hash.clone(), path.clone());
        }
        let media = media_type(&path);
        let mut e = serde_json::json!({
            "path": path, "bytes": bytes.len(), "sha256": hash, "media_type": media
        });
        if let Some(a) = alias_of {
            e["alias_of"] = serde_json::Value::String(a);
        }
        entries.push(e);
    }
    let manifest = serde_json::json!({
        "schema": "gw.science.corpus",
        "version": 1,
        "collection_id": collection,
        "captured_at": captured_at,
        "source": {
            "supplied_path": source.to_string_lossy(),
            "canonical_path": root.to_string_lossy(),
            "identity": format!("canonical-path:{}", root.to_string_lossy())
        },
        "selection": { "includes": includes },
        "originals": entries
    });
    if let Some(parent) = output.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let data = serde_json::to_string_pretty(&manifest).map_err(|e| e.to_string())? + "\n";
    fs::write(output, data).map_err(|e| e.to_string())
}

fn resolve_output(output: &Path) -> Result<PathBuf, String> {
    let absolute = if output.is_absolute() {
        output.to_path_buf()
    } else {
        std::env::current_dir()
            .map_err(|e| e.to_string())?
            .join(output)
    };
    let lexical = normalize_path(&absolute);
    if output.exists() {
        return fs::canonicalize(output).map_err(|e| e.to_string());
    }
    let mut existing = lexical.clone();
    let mut suffix = Vec::new();
    while !existing.exists() {
        suffix.push(
            existing
                .file_name()
                .ok_or("invalid output path")?
                .to_os_string(),
        );
        existing.pop();
    }
    let mut resolved = fs::canonicalize(existing).map_err(|e| e.to_string())?;
    for part in suffix.iter().rev() {
        resolved.push(part);
    }
    Ok(resolved)
}

fn normalize_path(path: &Path) -> PathBuf {
    let mut result = PathBuf::new();
    for component in path.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                result.pop();
            }
            other => result.push(other.as_os_str()),
        }
    }
    result
}

fn valid_captured_at(value: &str) -> bool {
    let bytes = value.as_bytes();
    if bytes.len() < 20
        || bytes[4] != b'-'
        || bytes[7] != b'-'
        || bytes[10] != b'T'
        || bytes[13] != b':'
        || bytes[16] != b':'
    {
        return false;
    }
    for &i in &[0usize, 1, 2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17, 18] {
        if !bytes[i].is_ascii_digit() {
            return false;
        }
    }
    let hour = value[11..13].parse::<u8>().unwrap();
    let minute = value[14..16].parse::<u8>().unwrap();
    let second = value[17..19].parse::<u8>().unwrap();
    if hour > 23 || minute > 59 || second > 60 {
        return false;
    }
    let rest = &value[19..];
    if rest == "Z" {
        return true;
    }
    if rest.len() >= 6
        && (rest.as_bytes()[0] == b'+' || rest.as_bytes()[0] == b'-')
        && rest.as_bytes()[3] == b':'
        && rest[1..3].parse::<u8>().map(|x| x <= 23).unwrap_or(false)
        && rest[4..6].parse::<u8>().map(|x| x <= 59).unwrap_or(false)
    {
        return rest.len() == 6;
    }
    if rest.starts_with('.') {
        let end = rest
            .find('Z')
            .or_else(|| rest.find('+'))
            .or_else(|| rest.find('-'));
        return end
            .map(|i| {
                i > 1
                    && rest[1..i].bytes().all(|b| b.is_ascii_digit())
                    && valid_captured_at(&format!("{}Z", &value[..19]))
            })
            .unwrap_or(false);
    }
    false
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
        if md.file_type().is_symlink()
            && !fs::canonicalize(&p)
                .map_err(|e| e.to_string())?
                .starts_with(root)
        {
            return Err("source contains a symlink outside root".into());
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

fn validate_include(value: &str) -> Result<String, String> {
    if value.is_empty() {
        return Err("include path must not be empty".into());
    }
    let path = Path::new(value);
    if path.is_absolute() {
        return Err("include path must be root-relative".into());
    }
    let mut clean = Vec::new();
    for component in path.components() {
        match component {
            Component::Normal(part) => clean.push(part.to_string_lossy().into_owned()),
            Component::CurDir => {}
            Component::ParentDir => {
                return Err("include path must not contain parent traversal".into())
            }
            _ => return Err("include path must be root-relative".into()),
        }
    }
    if clean.is_empty() {
        return Err("include path must not be empty".into());
    }
    Ok(clean.join("/"))
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
