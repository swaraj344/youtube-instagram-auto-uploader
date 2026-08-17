"""Flask app factory + routes for the local config UI (localhost only)."""

from __future__ import annotations

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from config import ConfigError
from webapp import services

SECTION_META = {
    "sources": {"label": "Sources", "singular": "source"},
    "youtube": {"label": "YouTube channels", "singular": "YouTube channel"},
    "instagram": {"label": "Instagram accounts", "singular": "Instagram account"},
}
_SEC = "<any(sources, youtube, instagram):section>"


def create_app() -> Flask:
    app = Flask(__name__)
    # Only signs the flash-message cookie; the app never leaves 127.0.0.1.
    app.secret_key = "local-config-ui"

    def _sources_for_forms():
        return [
            {"id": s["id"], "name": s["name"]}
            for s in services.list_entities("sources")
        ]

    @app.get("/")
    def dashboard():
        pull_error = services.refresh_state()
        return render_template(
            "dashboard.html",
            sources=services.list_entities("sources"),
            youtube=services.list_entities("youtube"),
            instagram=services.list_entities("instagram"),
            deploy=services.deploy_status(),
            meta_days=services.meta_token_days(),
            pull_error=pull_error,
            migration=services.migration_available(),
        )

    @app.get(f"/{_SEC}")
    def entity_list(section):
        return render_template(
            "entity_list.html",
            section=section,
            meta=SECTION_META[section],
            rows=services.list_entities(section),
        )

    @app.get(f"/{_SEC}/new")
    def new_entity(section):
        return render_template(
            "entity_form.html", section=section, meta=SECTION_META[section],
            e=None, form={}, sources=_sources_for_forms(),
        )

    @app.post(f"/{_SEC}/new")
    def create_entity(section):
        eid = (request.form.get("id") or "").strip().lower()
        try:
            services.upsert_entity(section, eid, request.form, new=True)
        except ConfigError as exc:
            flash(str(exc), "error")
            return render_template(
                "entity_form.html", section=section, meta=SECTION_META[section],
                e=None, form=request.form, sources=_sources_for_forms(),
            ), 400
        flash(f"Saved '{eid}' locally — deploy to apply.", "ok")
        return redirect(url_for("edit_entity", section=section, eid=eid))

    @app.get(f"/{_SEC}/<eid>/edit")
    def edit_entity(section, eid):
        e = services.get_entity(section, eid)
        if e is None:
            flash(f"No {SECTION_META[section]['singular']} '{eid}'.", "error")
            return redirect(url_for("entity_list", section=section))
        return render_template(
            "entity_form.html", section=section, meta=SECTION_META[section],
            e=e, form={}, sources=_sources_for_forms(),
        )

    @app.post(f"/{_SEC}/<eid>/edit")
    def update_entity(section, eid):
        try:
            services.upsert_entity(section, eid, request.form, new=False)
        except ConfigError as exc:
            flash(str(exc), "error")
            return render_template(
                "entity_form.html", section=section, meta=SECTION_META[section],
                e=services.get_entity(section, eid), form=request.form,
                sources=_sources_for_forms(),
            ), 400
        flash("Saved locally — deploy to apply.", "ok")
        return redirect(url_for("edit_entity", section=section, eid=eid))

    @app.post(f"/{_SEC}/<eid>/delete")
    def delete_entity(section, eid):
        try:
            services.delete_entity(section, eid)
            flash(f"Removed '{eid}' locally — deploy to apply.", "ok")
        except ConfigError as exc:
            flash(str(exc), "error")
        return redirect(url_for("entity_list", section=section))

    @app.post(f"/{_SEC}/<eid>/toggle")
    def toggle_entity(section, eid):
        if section == "sources":
            abort(404)
        services.toggle_entity(section, eid)
        return redirect(url_for("entity_list", section=section))

    @app.post(f"/{_SEC}/<eid>/connect-google")
    def connect_google(section, eid):
        try:
            services.connect_google(section, eid)
            flash("Google account connected — token stored locally. Deploy to apply.", "ok")
        except Exception as exc:
            flash(f"OAuth failed: {exc}", "error")
        return redirect(url_for("edit_entity", section=section, eid=eid))

    @app.post(f"/{_SEC}/<eid>/action")
    def entity_action(section, eid):
        try:
            services.trigger_action(
                eid, request.form.get("action", ""), request.form.get("upload_slot", "")
            )
            flash("Dispatched — watch Telegram for progress.", "ok")
        except Exception as exc:
            flash(f"Dispatch failed: {exc}", "error")
        return redirect(url_for("entity_list", section=section))

    @app.get("/api/pick-folder")
    def pick_folder():
        # Opens the native macOS folder picker; blocks until chosen/cancelled.
        return jsonify({"path": services.pick_local_folder()})

    @app.post("/sources/<eid>/upload")
    def start_source_upload(eid):
        try:
            services.start_upload(eid, request.form.get("local_folder", ""))
            flash("Upload started — progress shows below.", "ok")
        except Exception as exc:
            flash(f"Upload not started: {exc}", "error")
        return redirect(url_for("edit_entity", section="sources", eid=eid))

    @app.get("/api/sources/<eid>/upload-status")
    def source_upload_status(eid):
        return jsonify(services.upload_status(eid) or {"status": "idle"})

    @app.get("/api/ig-accounts")
    def ig_accounts():
        try:
            return jsonify(services.list_ig_accounts())
        except Exception as exc:
            return jsonify({"error": str(exc)}), 502

    @app.get("/settings")
    def settings():
        return render_template("settings.html", values=services.shared_settings())

    @app.post("/settings")
    def save_settings():
        services.save_shared_settings(request.form)
        flash("Saved locally — deploy to sync changed secrets to GitHub.", "ok")
        return redirect(url_for("settings"))

    @app.get("/deploy")
    def deploy_preview():
        return render_template("deploy.html", plan=services.deploy_plan())

    @app.post("/deploy")
    def deploy():
        for line in services.deploy():
            flash(line, "ok")
        return redirect(url_for("dashboard"))

    @app.post("/migrate")
    def migrate():
        try:
            flash(services.run_migration(), "ok")
        except Exception as exc:
            flash(f"Migration failed: {exc}", "error")
        return redirect(url_for("dashboard"))

    return app
