use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::{AtomicUsize, Ordering};

static TEMP_ROOT_ID: AtomicUsize = AtomicUsize::new(0);

fn run(args: &[&str]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_gw"))
        .args(args)
        .output()
        .unwrap()
}

fn temp_root() -> PathBuf {
    let path = std::env::temp_dir()
        .join(format!(
            "gw-freeze-{}-{}",
            std::process::id(),
            TEMP_ROOT_ID.fetch_add(1, Ordering::Relaxed)
        ))
        .join("source");
    let _ = fs::remove_dir_all(&path);
    fs::create_dir_all(&path).unwrap();
    path
}

#[test]
fn synthetic_freeze_is_deterministic_and_records_provenance() {
    let root = temp_root();
    fs::create_dir_all(root.join("nested")).unwrap();
    fs::write(root.join("a.pdf"), b"same").unwrap();
    fs::write(root.join("nested/alias.mp4"), b"same").unwrap();
    fs::write(root.join("nested/other.txt"), b"different").unwrap();
    let out1 = root.parent().unwrap().join("gw-manifest-1.json");
    let out2 = root.parent().unwrap().join("gw-manifest-2.json");
    let args = [
        "corpus",
        "freeze",
        "--collection",
        "demo",
        "--source",
        root.to_str().unwrap(),
        "--out",
        out1.to_str().unwrap(),
        "--capture-time",
        "2026-09-20T21:10:00Z",
    ];
    assert!(run(&args).status.success());
    let mut args2 = args;
    args2[7] = out2.to_str().unwrap();
    assert!(run(&args2).status.success());
    assert_eq!(fs::read(&out1).unwrap(), fs::read(&out2).unwrap());
    let json: serde_json::Value = serde_json::from_slice(&fs::read(&out1).unwrap()).unwrap();
    assert_eq!(json["source"]["canonical_path"].as_str().is_some(), true);
    assert_eq!(json["captured_at"], "2026-09-20T21:10:00Z");
    assert_eq!(json["originals"].as_array().unwrap().len(), 3);
    assert!(json["originals"]
        .as_array()
        .unwrap()
        .iter()
        .any(|e| e["alias_of"].is_string()));
    let _ = fs::remove_dir_all(root);
    let _ = fs::remove_file(out1);
    let _ = fs::remove_file(out2);
}

#[test]
fn validation_rejects_required_args_missing_root_and_output_inside_source() {
    let root = temp_root();
    let outside = root.parent().unwrap().join("gw-missing-root");
    let missing = run(&["corpus", "freeze", "--source", root.to_str().unwrap()]);
    assert!(!missing.status.success());
    assert!(!run(&[
        "corpus",
        "freeze",
        "--collection",
        "x",
        "--source",
        outside.to_str().unwrap(),
        "--out",
        "manifest.json"
    ])
    .status
    .success());
    assert!(!run(&[
        "corpus",
        "freeze",
        "--collection",
        "x",
        "--source",
        root.to_str().unwrap(),
        "--out",
        root.join("new/manifest.json").to_str().unwrap()
    ])
    .status
    .success());
    assert!(!run(&[
        "corpus",
        "freeze",
        "--collection",
        "x",
        "--source",
        root.to_str().unwrap(),
        "--out",
        root.parent().unwrap().join("bad.json").to_str().unwrap(),
        "--capture-time",
        "not-a-time"
    ])
    .status
    .success());
    let _ = fs::remove_dir_all(root);
}

#[cfg(unix)]
#[test]
fn validation_rejects_symlink_escape() {
    use std::os::unix::fs::symlink;
    let root = temp_root();
    let outside = root
        .parent()
        .unwrap()
        .join(format!("gw-escape-{}", std::process::id()));
    fs::create_dir_all(&outside).unwrap();
    fs::write(outside.join("secret.txt"), b"secret").unwrap();
    symlink(&outside, root.join("escape")).unwrap();
    let out = root.parent().unwrap().join("gw-symlink.json");
    assert!(!run(&[
        "corpus",
        "freeze",
        "--collection",
        "x",
        "--source",
        root.to_str().unwrap(),
        "--out",
        out.to_str().unwrap()
    ])
    .status
    .success());
    let _ = fs::remove_dir_all(root);
    let _ = fs::remove_dir_all(outside);
    let _ = fs::remove_file(out);
}

#[test]
fn explicit_includes_select_only_files_and_preserve_request_order() {
    let root = temp_root();
    fs::create_dir_all(root.join("nested")).unwrap();
    fs::write(root.join("a.txt"), b"a").unwrap();
    fs::write(root.join("nested/b.txt"), b"b").unwrap();
    fs::write(root.join("ignored.txt"), b"ignored").unwrap();
    let out = root.parent().unwrap().join("gw-includes.json");
    let args = [
        "corpus",
        "freeze",
        "--collection",
        "x",
        "--source",
        root.to_str().unwrap(),
        "--out",
        out.to_str().unwrap(),
        "--include",
        "nested/b.txt",
        "--include",
        "a.txt",
    ];
    assert!(run(&args).status.success());
    let json: serde_json::Value = serde_json::from_slice(&fs::read(&out).unwrap()).unwrap();
    assert_eq!(
        json["selection"]["includes"],
        serde_json::json!(["nested/b.txt", "a.txt"])
    );
    let paths: Vec<_> = json["originals"]
        .as_array()
        .unwrap()
        .iter()
        .map(|e| e["path"].as_str().unwrap())
        .collect();
    assert_eq!(paths, ["a.txt", "nested/b.txt"]);
    let _ = fs::remove_dir_all(root);
    let _ = fs::remove_file(out);
}

#[test]
fn include_validation_rejects_invalid_selections() {
    let root = temp_root();
    fs::create_dir_all(root.join("dir")).unwrap();
    fs::write(root.join("file.txt"), b"x").unwrap();
    for include in ["", "../file.txt", "missing.txt", "dir"] {
        let out = root.parent().unwrap().join("gw-invalid-include.json");
        assert!(
            !run(&[
                "corpus",
                "freeze",
                "--collection",
                "x",
                "--source",
                root.to_str().unwrap(),
                "--out",
                out.to_str().unwrap(),
                "--include",
                include
            ])
            .status
            .success(),
            "{include}"
        );
        let _ = fs::remove_file(&out);
    }
    let absolute = root.join("file.txt");
    let out = root.parent().unwrap().join("gw-invalid-absolute.json");
    assert!(!run(&[
        "corpus",
        "freeze",
        "--collection",
        "x",
        "--source",
        root.to_str().unwrap(),
        "--out",
        out.to_str().unwrap(),
        "--include",
        absolute.to_str().unwrap()
    ])
    .status
    .success());
    let _ = fs::remove_dir_all(root);
    let _ = fs::remove_file(out);
}

#[cfg(unix)]
#[test]
fn include_validation_rejects_symlink() {
    use std::os::unix::fs::symlink;
    let root = temp_root();
    fs::write(root.join("real.txt"), b"x").unwrap();
    symlink(root.join("real.txt"), root.join("link.txt")).unwrap();
    let out = root.parent().unwrap().join("gw-invalid-link.json");
    assert!(!run(&[
        "corpus",
        "freeze",
        "--collection",
        "x",
        "--source",
        root.to_str().unwrap(),
        "--out",
        out.to_str().unwrap(),
        "--include",
        "link.txt"
    ])
    .status
    .success());
    let _ = fs::remove_dir_all(root);
    let _ = fs::remove_file(out);
}
