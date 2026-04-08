import os
import uuid
import json
from flask import Flask, request, jsonify, render_template, send_file, Response
import config
from models.estimate import ParsedEstimate, EstimateMaterial, ProjectInfo
from models.finish_schedule import CrossRefResult
from services.pdf_extractor import extract_text, get_page_count, render_page_image
from services.ai_parser import parse_estimate
from services.mlt_filler import fill_mlt
from services.product_links import generate_product_links
from services.plans_parser import scan_for_finish_pages, parse_finish_schedule_pages
from services.cross_reference_service import cross_reference
from services.web_lookup import lookup_box_quantity

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH

# In-memory caches
_parsed_cache: dict[str, tuple[ParsedEstimate, str]] = {}  # job_id -> (estimate, pdf_path)
_plans_cache: dict[str, dict] = {}  # plans_id -> {pdf_path, page_count, scan_results, parsed_data}
_crossref_cache: dict[str, CrossRefResult] = {}  # job_id -> cross-ref result


@app.route("/")
def index():
    return render_template("index.html")


# ──────────────────────────────────────
# ESTIMATE ROUTES (existing)
# ──────────────────────────────────────

@app.route("/parse", methods=["POST"])
def parse():
    """Upload estimate PDF, extract text, parse with AI, return structured data."""
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Please upload a PDF file"}), 400

    job_id = str(uuid.uuid4())[:8]
    filename = f"{job_id}_{file.filename}"
    filepath = os.path.join(config.UPLOAD_FOLDER, filename)
    file.save(filepath)

    try:
        raw_text = extract_text(filepath)
        if not raw_text.strip():
            return jsonify({"error": "Could not extract text from PDF"}), 400

        estimate = parse_estimate(raw_text)
        _parsed_cache[job_id] = (estimate, filepath)

        return jsonify({
            "job_id": job_id,
            "project": estimate.project.to_dict(),
            "materials": [m.to_dict() for m in estimate.materials],
            "material_count": len(estimate.materials),
        })

    except Exception as e:
        if os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({"error": f"Parsing failed: {str(e)}"}), 500


@app.route("/update-materials", methods=["POST"])
def update_materials():
    """Update cached materials with user edits."""
    data = request.get_json()
    job_id = data.get("job_id")
    materials_data = data.get("materials", [])
    project_data = data.get("project", {})

    if job_id not in _parsed_cache:
        return jsonify({"error": "Session expired. Please re-upload."}), 400

    estimate, filepath = _parsed_cache[job_id]

    if project_data:
        estimate.project = ProjectInfo(**{
            k: v for k, v in project_data.items()
            if k in ProjectInfo.__dataclass_fields__
        })

    estimate.materials = [EstimateMaterial.from_dict(m) for m in materials_data]
    _parsed_cache[job_id] = (estimate, filepath)

    return jsonify({"status": "ok", "material_count": len(estimate.materials)})


@app.route("/generate", methods=["POST"])
def generate():
    """Generate MLT and Product Data Links from cached data."""
    data = request.get_json()
    job_id = data.get("job_id")
    start_date = data.get("start_date", "")
    project_name = data.get("project_name", "")

    if job_id not in _parsed_cache:
        return jsonify({"error": "Session expired. Please re-upload."}), 400

    estimate, filepath = _parsed_cache[job_id]
    name = project_name or estimate.project.project_name or "Project"

    try:
        # Get cross-ref result if available
        cross_ref = _crossref_cache.get(job_id)

        # Generate MLT (with cross-ref if available)
        mlt_path = fill_mlt(estimate, start_date, name, cross_ref)
        mlt_filename = os.path.basename(mlt_path)

        # Generate Product Data Links
        links_path = generate_product_links(estimate, name)
        links_filename = os.path.basename(links_path)

        return jsonify({
            "mlt_file": mlt_filename,
            "links_file": links_filename,
            "has_crossref": cross_ref is not None,
        })

    except Exception as e:
        return jsonify({"error": f"Generation failed: {str(e)}"}), 500


# ──────────────────────────────────────
# PLANS ROUTES (new)
# ──────────────────────────────────────

@app.route("/upload-plans", methods=["POST"])
def upload_plans():
    """Upload an IFC plans PDF."""
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Please upload a PDF file"}), 400

    plans_id = str(uuid.uuid4())[:8]
    filename = f"plans_{plans_id}_{file.filename}"
    filepath = os.path.join(config.UPLOAD_FOLDER, filename)
    file.save(filepath)

    try:
        page_count = get_page_count(filepath)
        _plans_cache[plans_id] = {
            "pdf_path": filepath,
            "page_count": page_count,
            "scan_results": None,
            "parsed_data": None,
        }

        return jsonify({
            "plans_id": plans_id,
            "page_count": page_count,
            "filename": file.filename,
        })

    except Exception as e:
        if os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({"error": f"Upload failed: {str(e)}"}), 500


@app.route("/scan-plans", methods=["POST"])
def scan_plans():
    """Scan uploaded plans PDF for finish schedule pages."""
    data = request.get_json()
    plans_id = data.get("plans_id")

    if plans_id not in _plans_cache:
        return jsonify({"error": "Plans not found. Please re-upload."}), 400

    plans = _plans_cache[plans_id]

    try:
        candidates = scan_for_finish_pages(plans["pdf_path"])
        plans["scan_results"] = candidates

        return jsonify({
            "plans_id": plans_id,
            "candidates": candidates,
            "total_found": len(candidates),
        })

    except Exception as e:
        return jsonify({"error": f"Scan failed: {str(e)}"}), 500


@app.route("/plans-page-preview/<plans_id>/<int:page_num>")
def plans_page_preview(plans_id, page_num):
    """Render a specific page of the plans as a JPEG thumbnail."""
    if plans_id not in _plans_cache:
        return jsonify({"error": "Plans not found"}), 404

    plans = _plans_cache[plans_id]

    try:
        img_bytes = render_page_image(plans["pdf_path"], page_num, dpi=100)
        return Response(img_bytes, mimetype="image/jpeg")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/parse-plans", methods=["POST"])
def parse_plans_route():
    """Parse selected finish schedule pages using AI."""
    data = request.get_json()
    plans_id = data.get("plans_id")
    pages = data.get("pages", [])

    if plans_id not in _plans_cache:
        return jsonify({"error": "Plans not found. Please re-upload."}), 400

    if not pages:
        return jsonify({"error": "No pages selected to parse"}), 400

    plans = _plans_cache[plans_id]

    try:
        parsed_data = parse_finish_schedule_pages(plans["pdf_path"], pages)
        plans["parsed_data"] = parsed_data

        return jsonify({
            "plans_id": plans_id,
            "material_definitions": parsed_data.get("material_definitions", []),
            "room_assignments": parsed_data.get("room_assignments", []),
            "material_legends": parsed_data.get("material_legends", {}),
            "summary": {
                "materials_found": len(parsed_data.get("material_definitions", [])),
                "rooms_found": len(parsed_data.get("room_assignments", [])),
                "legend_entries": len(parsed_data.get("material_legends", {})),
            }
        })

    except Exception as e:
        return jsonify({"error": f"Parsing failed: {str(e)}"}), 500


# ──────────────────────────────────────
# CROSS-REFERENCE ROUTES (new)
# ──────────────────────────────────────

@app.route("/cross-reference", methods=["POST"])
def cross_reference_route():
    """Run cross-reference between estimate and plans data."""
    data = request.get_json()
    job_id = data.get("job_id")
    plans_id = data.get("plans_id")

    if job_id not in _parsed_cache:
        return jsonify({"error": "Estimate data not found. Please re-upload."}), 400

    if plans_id not in _plans_cache:
        return jsonify({"error": "Plans data not found. Please re-upload."}), 400

    plans = _plans_cache[plans_id]
    if not plans.get("parsed_data"):
        return jsonify({"error": "Plans not yet parsed. Parse finish schedule pages first."}), 400

    estimate, _ = _parsed_cache[job_id]
    parsed_data = plans["parsed_data"]

    try:
        result = cross_reference(
            estimate_materials=estimate.materials,
            material_definitions=parsed_data.get("material_definitions", []),
            room_assignments=parsed_data.get("room_assignments", []),
            material_legends=parsed_data.get("material_legends", {}),
        )

        # Cache for generation
        _crossref_cache[job_id] = result

        return jsonify(result.to_dict())

    except Exception as e:
        return jsonify({"error": f"Cross-reference failed: {str(e)}"}), 500


# ──────────────────────────────────────
# PRODUCT DATA LOOKUP ROUTES (new)
# ──────────────────────────────────────

@app.route("/lookup-box-qty", methods=["POST"])
def lookup_box_qty():
    """Look up box/carton quantities for all materials in the estimate."""
    data = request.get_json()
    job_id = data.get("job_id")

    if job_id not in _parsed_cache:
        return jsonify({"error": "Session expired. Please re-upload."}), 400

    estimate, filepath = _parsed_cache[job_id]
    results = []
    found_count = 0

    for i, mat in enumerate(estimate.materials):
        if mat.vendor.upper() == "TBD" or not mat.vendor:
            results.append({"index": i, "code": mat.product_code, "status": "skipped"})
            continue

        result = lookup_box_quantity(mat.vendor, mat.selection, mat.color, mat.size)
        if result and result.get("box_qty", 0) > 0:
            mat.box_qty = result["box_qty"]
            mat.box_qty_unit = result.get("unit", "SF")
            found_count += 1
            results.append({
                "index": i,
                "code": mat.product_code,
                "status": "found",
                "box_qty": result["box_qty"],
                "unit": result.get("unit", "SF"),
                "source_url": result.get("source_url", ""),
            })
        else:
            results.append({"index": i, "code": mat.product_code, "status": "not_found"})

    # Update cache
    _parsed_cache[job_id] = (estimate, filepath)

    return jsonify({
        "job_id": job_id,
        "total": len(estimate.materials),
        "found": found_count,
        "results": results,
    })


# ──────────────────────────────────────
# UTILITY ROUTES
# ──────────────────────────────────────

@app.route("/test-pdf")
def test_pdf():
    """Serve the test PDF for development testing."""
    test_path = os.path.join(
        os.path.dirname(config.BASE_DIR),
        "..", "Daniel_Claude", "FINAL Alliance The Gallery.pdf"
    )
    test_path = os.path.normpath(test_path)
    if os.path.exists(test_path):
        return send_file(test_path)
    return jsonify({"error": "Test PDF not found"}), 404


@app.route("/download/<filename>")
def download(filename):
    """Download a generated file."""
    filepath = os.path.join(config.OUTPUT_FOLDER, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404
    return send_file(filepath, as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True, port=5051)
