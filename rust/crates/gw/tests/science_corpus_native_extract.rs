use sha2::{Digest, Sha256};
use std::{fs, process::Command};

fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

#[test]
fn native_fake_tools_extract_pages_and_pending_video() {
    let root = std::env::temp_dir().join(format!("gw-native-{}", std::process::id()));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();
    let pdf = b"synthetic pdf";
    let video = b"synthetic video";
    fs::write(root.join("paper.pdf"), pdf).unwrap();
    fs::write(root.join("clip.mp4"), video).unwrap();
    let pdfinfo = root.join("pdfinfo.cmd");
    let pdftotext = root.join("pdftotext.cmd");
    fs::write(&pdfinfo, "@echo Pages: 3\r\n").unwrap();
    fs::write(
        &pdftotext,
        "@if \"%2\"==\"2\" exit /b 0\r\n@if \"%2\"==\"3\" exit /b 1\r\n@echo page-one text\r\n",
    )
    .unwrap();
    let manifest = serde_json::json!({"source":{"supplied_path":root},"extractor":{"name":"freeze","version":"1"},"originals":[
        {"path":"paper.pdf","bytes":pdf.len(),"sha256":hash(pdf),"media_type":"application/pdf"},
        {"path":"clip.mp4","bytes":video.len(),"sha256":hash(video),"media_type":"video/mp4"}]});
    let mp = root.join("manifest.json");
    fs::write(&mp, serde_json::to_vec(&manifest).unwrap()).unwrap();
    let out = root
        .parent()
        .unwrap()
        .join(format!("gw-native-out-{}", std::process::id()));
    let _ = fs::remove_dir_all(&out);
    let result = Command::new(env!("CARGO_BIN_EXE_gw"))
        .args([
            "corpus",
            "extract",
            "--manifest",
            mp.to_str().unwrap(),
            "--out",
            out.to_str().unwrap(),
            "--captured-at",
            "2026-09-21T00:00:00Z",
            "--pdfinfo",
            pdfinfo.to_str().unwrap(),
            "--pdftotext",
            pdftotext.to_str().unwrap(),
        ])
        .output()
        .unwrap();
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    let artifact: serde_json::Value =
        serde_json::from_slice(&fs::read(out.join("extraction.json")).unwrap()).unwrap();
    let pages = &artifact["results"][0]["pages"];
    assert_eq!(pages[0]["status"], "extracted");
    assert_eq!(pages[1]["status"], "ocr_required");
    assert_eq!(pages[2]["status"], "retry");
    assert_eq!(artifact["results"][1]["status"], "pending");
    assert!(artifact["tools"]["pdfinfo"]["executable"]
        .as_str()
        .unwrap()
        .contains("pdfinfo"));
    let _ = fs::remove_dir_all(root);
    let _ = fs::remove_dir_all(out);
}
