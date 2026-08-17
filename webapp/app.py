"""Flask app factory + routes for the local config UI (localhost only)."""

from __future__ import annotations

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from config import ConfigError
from webapp import services


def create_app() -> Flask:
    app = Flask(__name__)
    # Only signs the flash-message cookie; the app never leaves 127.0.0.1.
    app.secret_key = "local-config-ui"

    @app.get("/")
    def dashboard():
        pull_error = services.refresh_state()
        return render_template(
            "dashboard.html",
            channels=services.channels_with_status(),
            deploy=services.deploy_status(),
            meta_days=services.meta_token_days(),
            pull_error=pull_error,
            legacy=services.legacy_import_available(),
        )

    @app.get("/channels/new")
    def new_channel():
        return render_template("channel_form.html", ch=None, form={})

    @app.post("/channels/new")
    def create_channel():
        slug = (request.form.get("slug") or "").strip().lower()
        try:
            services.upsert_channel(slug, request.form, new=True)
        except ConfigError as exc:
            flash(str(exc), "error")
            return render_template("channel_form.html", ch=None, form=request.form), 400
        flash(f"Channel '{slug}' saved locally — deploy to apply.", "ok")
        return redirect(url_for("edit_channel", slug=slug))

    @app.get("/channels/<slug>/edit")
    def edit_channel(slug):
        ch = services.get_channel(slug)
        if ch is None:
            flash(f"No channel '{slug}'.", "error")
            return redirect(url_for("dashboard"))
        return render_template("channel_form.html", ch=ch, form={})

    @app.post("/channels/<slug>/edit")
    def update_channel(slug):
        try:
            services.upsert_channel(slug, request.form, new=False)
        except ConfigError as exc:
            flash(str(exc), "error")
            return (
                render_template(
                    "channel_form.html", ch=services.get_channel(slug), form=request.form
                ),
                400,
            )
        flash("Saved locally — deploy to apply.", "ok")
        return redirect(url_for("edit_channel", slug=slug))

    @app.post("/channels/<slug>/toggle")
    def toggle_channel(slug):
        services.toggle_channel(slug)
        return redirect(url_for("dashboard"))

    @app.post("/channels/<slug>/delete")
    def delete_channel(slug):
        services.delete_channel(slug)
        flash(
            f"Channel '{slug}' removed locally — deploy to apply. "
            "Its state/ files were kept on disk.",
            "ok",
        )
        return redirect(url_for("dashboard"))

    @app.post("/channels/<slug>/connect-youtube")
    def connect_youtube(slug):
        try:
            services.connect_youtube(slug)
            flash("YouTube connected — token stored locally. Deploy to apply.", "ok")
        except Exception as exc:
            flash(f"OAuth failed: {exc}", "error")
        return redirect(url_for("edit_channel", slug=slug))

    @app.get("/api/ig-accounts")
    def ig_accounts():
        try:
            return jsonify(services.list_ig_accounts())
        except Exception as exc:
            return jsonify({"error": str(exc)}), 502

    @app.post("/channels/<slug>/action")
    def channel_action(slug):
        try:
            services.trigger_action(
                slug,
                request.form.get("action", ""),
                request.form.get("upload_slot", ""),
            )
            flash("Dispatched — watch Telegram for progress.", "ok")
        except Exception as exc:
            flash(f"Dispatch failed: {exc}", "error")
        return redirect(url_for("dashboard"))

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

    @app.post("/import")
    def legacy_import():
        try:
            flash(services.run_legacy_import(), "ok")
        except Exception as exc:
            flash(f"Import failed: {exc}", "error")
        return redirect(url_for("dashboard"))

    return app
