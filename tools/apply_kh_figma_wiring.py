#!/usr/bin/env python3
"""Anchored, idempotent, line-ending-preserving wiring of the Knowledge Hub "Figma" Source Type and "UI/UX Design" Document Type.

Usage: apply_kh_figma_wiring.py <index.html> <knowledge_web_repository.py> <knowledge_router.py>

Every edit names an exact anchor that must occur exactly once; if any anchor of a file is missing or ambiguous nothing is
written for that file. Existing source types, document types and upload paths are not modified or removed.
"""
import sys

MARK = "QA-AI-STUDIO-UIUX-KH"


def edit(path, edits):
    raw = open(path, "rb").read()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8")
    nl = "\r\n" if crlf else "\n"
    if MARK in text:
        print(f"{path}: already wired (marker present) - unchanged")
        return False
    out = text
    for name, old, new in edits:
        old_n, new_n = old.replace("\n", nl), new.replace("\n", nl)
        count = out.count(old_n)
        if count != 1:
            raise SystemExit(f"{path}: anchor '{name}' found {count} times (expected exactly 1); nothing written")
        out = out.replace(old_n, new_n)
    open(path, "wb").write(out.encode("utf-8"))
    print(f"{path}: {len(edits)} edits applied ({'CRLF' if crlf else 'LF'} preserved)")
    return True


INDEX = [
    ("document type",
     '<option value="Integration Document">Integration Document</option><option value="Other">Other</option></select></div>\n                  <!-- QA-AI-STUDIO-FINAL-ARCHITECTURE-UI-AUTOMATION-COMPLETION-V4',
     '<option value="Integration Document">Integration Document</option><option value="UI/UX Design">UI/UX Design</option><option value="Other">Other</option></select></div> <!-- QA-AI-STUDIO-UIUX-KH -->\n                  <!-- QA-AI-STUDIO-FINAL-ARCHITECTURE-UI-AUTOMATION-COMPLETION-V4'),
    ("source type",
     '<option value="URL">URL / Guided Discovery</option><option value="Images" disabled>',
     '<option value="URL">URL / Guided Discovery</option><option value="Figma">Figma</option><option value="Images" disabled>'),
    ("edit document type",
     '<option>Technical Document</option><option>Other</option></select></div>',
     '<option>Technical Document</option><option>UI/UX Design</option><option>Other</option></select></div>'),
    ("help text",
     'Other source types remain unavailable until their real processing paths exist.</div>',
     'Other source types remain unavailable until their real processing paths exist.</div>\n                <div class="kh-section-help" id="khUpFigmaHelp" hidden>Figma designs are stored in Knowledge Hub and used by QA Automation &rarr; AI UI/UX Testing. Accepted: native .fig (stored unchanged; its structure is not analysed), Figma JSON export, PNG, JPEG and safe SVG. Document Type must be UI/UX Design. To connect a Figma file by link, use AI UI/UX Testing.</div>'),
    ("extensions",
     "var khSupportedUploadExtensions = ['pdf', 'docx', 'txt', 'xlsx'];",
     "var khSupportedUploadExtensions = ['pdf', 'docx', 'txt', 'xlsx'];\n  var khFigmaUploadExtensions = ['fig', 'json', 'png', 'jpg', 'jpeg', 'svg']; // QA-AI-STUDIO-UIUX-KH"),
    ("supported check",
     "var supported = selectedType === 'API Collection' ? extension === 'json' : khSupportedUploadExtensions.indexOf(extension) !== -1;",
     "var supported = selectedType === 'API Collection' ? extension === 'json' : selectedType === 'Figma' ? khFigmaUploadExtensions.indexOf(extension) !== -1 : khSupportedUploadExtensions.indexOf(extension) !== -1;"),
    ("accept",
     "input.accept=type==='API Collection'?'.json':'.pdf,.docx,.txt,.xlsx';",
     "input.accept=type==='API Collection'?'.json':type==='Figma'?'.fig,.json,.png,.jpg,.jpeg,.svg':'.pdf,.docx,.txt,.xlsx';"),
    ("allowed types",
     "if(['Files','Folder','Release Notes','Test Cases','SOP Documents','API Collection'].indexOf(selectedSourceType) === -1){",
     "if(['Files','Folder','Release Notes','Test Cases','SOP Documents','API Collection','Figma'].indexOf(selectedSourceType) === -1){"),
    ("figma doc type rule",
     "var uploadQueue = khUploadSources.filter(function(source){ return source.status !== 'Successful' && source.status !== 'Stored / Not Indexed'; });",
     "if(selectedSourceType === 'Figma' && metadata.documentType !== 'UI/UX Design'){\n      showKhUploadError('Figma designs must use Document Type \"UI/UX Design\".');\n      return;\n    }\n    var uploadQueue = khUploadSources.filter(function(source){ return source.status !== 'Successful' && source.status !== 'Stored / Not Indexed'; });"),
    ("source type change",
     "document.getElementById('khUpHiddenFile').addEventListener('change', function(){ addKhUploadSources(this.files); this.value = ''; });",
     "document.getElementById('khUpHiddenFile').addEventListener('change', function(){ addKhUploadSources(this.files); this.value = ''; });\n  document.getElementById('khUpSourceType').addEventListener('change', function(){\n    var figma = this.value === 'Figma', docType = document.getElementById('khUpDocTypeSel'), help = document.getElementById('khUpFigmaHelp');\n    if(help) help.hidden = !figma;\n    if(figma && docType.value !== 'UI/UX Design'){ docType.value = 'UI/UX Design'; docType.dispatchEvent(new Event('change')); }\n  });"),
]

REPOSITORY = [
    ("signature",
     "        reviewed_analysis=None,\n    ):\n\n        from Core.upload_pipeline import UploadPipeline\n",
     "        reviewed_analysis=None,\n        created_by=\"\",\n    ):\n\n        from Core.upload_pipeline import UploadPipeline\n"),
    ("allowed",
     "        allowed_source_types = {\"Files\"}\n        if source_type not in allowed_source_types:\n            raise ValueError(\"Unsupported document source type.\")\n",
     "        allowed_source_types = {\"Files\"}\n        # QA-AI-STUDIO-UIUX-KH: Source Type Figma (UI/UX Design assets) is handled by the shared design service below.\n        try:\n            from Core.uiux.kh_bridge import is_figma\n        except Exception:  # the design service is optional; every other source type is unaffected\n            def is_figma(value):\n                return False\n        if is_figma(source_type):\n            source_type = \"Figma\"\n            allowed_source_types.add(\"Figma\")\n        if source_type not in allowed_source_types:\n            raise ValueError(\"Unsupported document source type.\")\n"),
    ("figma branch",
     "            raise ValueError(f\"Required and missing: {problem}.\")\n\n        safe_name = Path(original_filename or \"upload\").name or \"upload\"\n        # A plain NamedTemporaryFile",
     "            raise ValueError(f\"Required and missing: {problem}.\")\n\n        if source_type == \"Figma\":\n            from Core.uiux.kh_bridge import upload_from_knowledge_hub\n            return upload_from_knowledge_hub(\n                file_bytes=file_bytes, original_filename=original_filename, domain=domain, module=module,\n                knowledge_name=knowledge_name, version=version, document_type=document_type, created_by=created_by,\n            )\n\n        safe_name = Path(original_filename or \"upload\").name or \"upload\"\n        # A plain NamedTemporaryFile"),
]

ROUTER = [
    ("import",
     "from Web.deps import require_permission\n",
     "from Web.deps import require_permission\n\ntry:  # QA-AI-STUDIO-UIUX-KH: optional AI UI/UX Testing design service (Source Type Figma)\n    from Core.uiux.kh_bridge import DesignServiceUnavailable\nexcept Exception:  # noqa: BLE001 - Knowledge Hub keeps working without the module\n    class DesignServiceUnavailable(Exception):\n        pass\n"),
    ("created_by",
     "            source_type=source_type,\n            reviewed_analysis=reviewed_analysis,\n        )\n",
     "            source_type=source_type,\n            reviewed_analysis=reviewed_analysis,\n            created_by=(current_user or {}).get(\"username\", \"\") if isinstance(current_user, dict) else \"\",\n        )\n"),
    ("unavailable",
     "    except ValueError as error:\n\n        raise HTTPException(status_code=400, detail=str(error))\n\n    _audit(\n        current_user,\n        \"UPLOAD_KNOWLEDGE_ITEM\",",
     "    except DesignServiceUnavailable as error:\n\n        raise HTTPException(status_code=503, detail=str(error))\n\n    except ValueError as error:\n\n        raise HTTPException(status_code=400, detail=str(error))\n\n    _audit(\n        current_user,\n        \"UPLOAD_KNOWLEDGE_ITEM\","),
]

if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    edit(sys.argv[1], INDEX)
    edit(sys.argv[2], REPOSITORY)
    edit(sys.argv[3], ROUTER)
