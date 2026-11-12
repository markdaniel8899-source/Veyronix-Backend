"""Tests for the universal file parser and unrestricted upload handling."""

import zipfile

import pytest

from app.api.graph import allowed_file
from app.utils.file_parser import FileParser


def _write_zip(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def test_every_file_type_is_accepted():
    assert allowed_file("report.pdf")
    assert allowed_file("report.docx")
    assert allowed_file("report.weird")
    assert allowed_file("file-without-extension")
    assert not allowed_file("")
    assert not allowed_file("   ")
    assert FileParser.is_supported("anything.xyz") is True


def test_reads_markdown_and_txt(tmp_path):
    markdown = tmp_path / "notes.md"
    markdown.write_text("# Heading\n\nMarkdown body.", encoding="utf-8")

    text = tmp_path / "notes.txt"
    text.write_text("Plain text body.", encoding="utf-8")

    assert "Markdown body." in FileParser.extract_text(str(markdown))
    assert "Plain text body." in FileParser.extract_text(str(text))


def test_reads_unknown_text_extension_and_extensionless_file(tmp_path):
    weird = tmp_path / "data.weird"
    weird.write_text("Unknown but readable text.", encoding="utf-8")

    no_extension = tmp_path / "README"
    no_extension.write_text("Extensionless text.", encoding="utf-8")

    assert "Unknown but readable text." in FileParser.extract_text(str(weird))
    assert "Extensionless text." in FileParser.extract_text(str(no_extension))


def test_reads_csv_json_and_xml(tmp_path):
    csv_file = tmp_path / "data.csv"
    csv_file.write_text("name,role\nAisha,CEO\n", encoding="utf-8")

    json_file = tmp_path / "data.json"
    json_file.write_text('{"name": "Aisha", "role": "CEO"}', encoding="utf-8")

    xml_file = tmp_path / "data.xml"
    xml_file.write_text(
        "<root><item>Alpha</item><item>Beta</item></root>",
        encoding="utf-8",
    )

    assert "Aisha" in FileParser.extract_text(str(csv_file))
    assert "CEO" in FileParser.extract_text(str(json_file))
    xml_text = FileParser.extract_text(str(xml_file))
    assert "Alpha" in xml_text and "Beta" in xml_text


def test_reads_html_without_scripts(tmp_path):
    html_file = tmp_path / "page.html"
    html_file.write_text(
        "<html><head><style>.x{color:red}</style>"
        "<script>badFunction()</script></head>"
        "<body><h1>Title</h1><p>Body &amp; more</p></body></html>",
        encoding="utf-8",
    )

    text = FileParser.extract_text(str(html_file))
    assert "Title" in text
    assert "Body & more" in text
    assert "badFunction" not in text
    assert ".x{color:red}" not in text


def test_reads_rtf(tmp_path):
    rtf_file = tmp_path / "note.rtf"
    rtf_file.write_text(
        r"{\rtf1\ansi\deff0 {\fonttbl {\f0 Arial;}}"
        r"\f0\fs24 Hello \b world\b0\par Second line}",
        encoding="utf-8",
    )

    text = FileParser.extract_text(str(rtf_file))
    assert "Hello" in text
    assert "world" in text
    assert "Second line" in text


def test_reads_docx_without_external_dependency(tmp_path):
    docx_file = tmp_path / "report.docx"
    _write_zip(
        docx_file,
        {
            "word/document.xml": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                'wordprocessingml/2006/main"><w:body>'
                "<w:p><w:r><w:t>Hello</w:t></w:r>"
                "<w:r><w:t> world</w:t></w:r></w:p>"
                "<w:p><w:r><w:t>Second paragraph</w:t></w:r></w:p>"
                "</w:body></w:document>"
            ),
        },
    )

    text = FileParser.extract_text(str(docx_file))
    assert "Hello world" in text
    assert "Second paragraph" in text


def test_reads_pptx_without_external_dependency(tmp_path):
    pptx_file = tmp_path / "slides.pptx"
    _write_zip(
        pptx_file,
        {
            "ppt/slides/slide1.xml": (
                '<p:sld xmlns:a="http://schemas.openxmlformats.org/'
                'drawingml/2006/main" xmlns:p="http://schemas.openxmlformats'
                '.org/presentationml/2006/main"><p:cSld><p:spTree><p:sp>'
                "<p:txBody><a:p><a:r><a:t>Slide text</a:t></a:r></a:p>"
                "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
            ),
        },
    )

    assert "Slide text" in FileParser.extract_text(str(pptx_file))


def test_reads_xlsx(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")

    xlsx_file = tmp_path / "data.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet["A1"] = "Name"
    sheet["B1"] = "Value"
    sheet["A2"] = "Alice"
    sheet["B2"] = 42
    workbook.save(xlsx_file)

    text = FileParser.extract_text(str(xlsx_file))
    assert "Alice" in text
    assert "42" in text


def test_binary_file_raises_clear_error(tmp_path):
    binary_file = tmp_path / "blob.png"
    binary_file.write_bytes(b"\x00\x01\x02\x03" * 4096)

    with pytest.raises(ValueError, match="No readable text"):
        FileParser.extract_text(str(binary_file))


def test_utf16_text_file_is_read(tmp_path):
    utf16_file = tmp_path / "utf16.txt"
    utf16_file.write_bytes("Hello UTF-16".encode("utf-16"))

    assert "Hello UTF-16" in FileParser.extract_text(str(utf16_file))


def test_empty_file_raises_clear_error(tmp_path):
    empty_file = tmp_path / "empty.weird"
    empty_file.write_bytes(b"")

    with pytest.raises(ValueError, match="empty"):
        FileParser.extract_text(str(empty_file))
