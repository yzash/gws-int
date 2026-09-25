"""Gemini deep-dive: use-case buckets, behaviour, time patterns, periods, groups."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .common import Section, after_hours_mask, heatmap, pct

# 13 use-case buckets, keyed on Google's documented `action` / `feature_source` values.
BUCKETS = [
    "Lesson and resource creation", "Assessment and feedback", "Differentiation and inclusion",
    "Building presentations", "Images and visuals", "Video creation", "Writing and communication",
    "Summarising and understanding", "Data and admin work", "Meetings",
    "Open conversation and research", "Automation and agents", "Gemini Notebook outputs",
]
FAMILY = {
    "Lesson and resource creation": "create", "Building presentations": "create",
    "Images and visuals": "create", "Video creation": "create",
    "Gemini Notebook outputs": "create", "Assessment and feedback": "assess",
    "Differentiation and inclusion": "differentiate", "Summarising and understanding": "summarise",
    "Writing and communication": "communicate", "Meetings": "communicate",
    "Open conversation and research": "converse", "Data and admin work": "automate",
    "Automation and agents": "automate", "Unclassified": "unclassified",
}
ACTION_BUCKET = {
    **{a: "Lesson and resource creation" for a in [
        "classic_use_case_generate_lesson_plan", "classic_use_case_generate_hooks",
        "classic_use_case_generate_informative_articles", "classic_use_case_generate_project_activities",
        "classic_use_case_generate_choice_board", "classic_use_case_generate_story",
        "classic_use_case_generate_vocab_list", "classic_use_case_generate_common_misconceptions"]},
    **{a: "Assessment and feedback" for a in [
        "classic_use_case_generate_questions", "classic_use_case_generate_text_dependent_questions",
        "classic_use_case_generate_rubric", "classic_use_case_convert_rubric",
        "classic_use_case_generate_feedback", "generate_form_questions", "generate_form"]},
    **{a: "Differentiation and inclusion" for a in [
        "classic_use_case_relevel_content", "classic_use_case_translate_text",
        "classic_use_case_generate_audio_lesson"]},
    "classic_use_case_generate_presentation": "Building presentations",
    **{a: "Images and visuals" for a in [
        "generate_image_for_current_page", "generate_images_in_product", "outpaint",
        "remove_background", "replace_background"]},
    **{a: "Video creation" for a in [
        "generate_videos_in_product", "generate_avatar_video", "classic_use_case_teleprompter_word_match"]},
    **{a: "Writing and communication" for a in [
        "generate_text", "generate_document", "live_generate_document", "proofread", "auto_proofread",
        "paraphrase", "formalize", "elaborate", "condense", "bulletize"]},
    **{a: "Summarising and understanding" for a in [
        "summarize", "summarize_file", "summarize_document_comment_thread",
        "summarize_drive_homepage_doclist_files", "summarize_drive_homepage_doclist_files_long",
        "summarize_proactive", "summarize_proactive_short", "proactive_daily_briefs"]},
    **{a: "Data and admin work" for a in [
        "generate_ai_function_response", "classic_use_case_sheets_turbofill", "generate_table_name",
        "suggest_time", "classic_use_case_suggest_time_reporting", "add_to_calendar"]},
    **{a: "Meetings" for a in [
        "classic_use_case_meet_take_notes_for_me_session", "classic_use_case_meet_studio_lighting",
        "classic_use_case_meet_studio_look", "classic_use_case_meet_studio_sound"]},
    **{a: "Open conversation and research" for a in [
        "conversation", "custom_prompt", "search_web", "describe_gemini_uses", "generate_nudge_prompts",
        "proactive_suggestions", "proactive_suggestions_response"]},
    "user_confirmed_tools_operation": "Automation and agents",
}
FEATURE_BUCKET = {
    "classroom_gemini_education": "Lesson and resource creation",
    "help_me_visualize": "Images and visuals", "generate_background": "Images and visuals",
    "remove_image_background": "Images and visuals",
    "help_me_write": "Writing and communication", "help_me_refine": "Writing and communication",
    "proofread": "Writing and communication",
    "search_ai_overview": "Summarising and understanding",
    "ai_function": "Data and admin work", "enhanced_smart_fill": "Data and admin work",
    "drive_help_me_organize": "Data and admin work", "help_me_schedule": "Data and admin work",
    "take_notes_for_me": "Meetings", "studio_light": "Meetings", "studio_look": "Meetings",
    "studio_sound": "Meetings",
    "ask_gemini": "Open conversation and research", "chat_with_gemini": "Open conversation and research",
    "workflows_creation": "Automation and agents", "workflows_execution": "Automation and agents",
}
APP_BUCKET = {
    "slides": "Building presentations", "vids": "Video creation", "meet": "Meetings",
    "sheets": "Data and admin work", "gemini_app": "Open conversation and research",
    "classroom": "Lesson and resource creation", "workflows": "Automation and agents",
    "gmail": "Writing and communication", "docs": "Writing and communication",
    "calendar": "Data and admin work", "forms": "Assessment and feedback", "keep": "Writing and communication",
}


def classify(action: str, feature: str, app: str, overrides: dict | None = None) -> str:
    overrides = overrides or {}
    for key in (action, feature):
        if key and key in overrides:
            return overrides[key]
    a = (action or "").lower()
    if "notebook" in a or "audio_overview" in a:
        return "Gemini Notebook outputs"
    # A generic action ("generate_text") in Slides is presentation building, etc.
    if a in ("generate_text", "generate_document", "generate_starter_freeform",
             "generate_starter_active_view", "generate_starter_tile_prompts") and app in APP_BUCKET \
            and app not in ("gmail", "docs"):
        return APP_BUCKET[app]
    if a in ACTION_BUCKET:
        return ACTION_BUCKET[a]
    if feature in FEATURE_BUCKET:
        return FEATURE_BUCKET[feature]
    if app in APP_BUCKET:
        return APP_BUCKET[app]
    return "Unclassified"


def add_buckets(events: pd.DataFrame, overrides=None) -> pd.DataFrame:
    ev = events.copy()
    ev["use_case"] = [classify(a, f, ap, overrides)
                      for a, f, ap in zip(ev["action"], ev["feature"], ev["app"])]
    ev["family"] = ev["use_case"].map(FAMILY).fillna("unclassified")
    return ev


def use_case_section(events: pd.DataFrame, summary: pd.DataFrame, cfg: dict) -> Section:
    s = Section("gemini_use_cases", "Gemini use cases and behaviour",
                intro="Each Gemini action is mapped to one of 13 use-case buckets from its "
                      "documented action, feature and app. Unmapped values are listed so they can "
                      "be assigned with use_case_map in config.yaml.")
    if events.empty:
        s.status, s.reason = "empty", "No Gemini events in the window."
        return s
    ev = add_buckets(events, cfg.get("use_case_map"))
    ai_users = summary[summary["total_actions"] > 0]
    window_days = max(int(cfg["window_days"]), 1)
    per_user_buckets = ev.groupby("user_email")["use_case"].nunique()
    median_days_month = float(np.median(ai_users["active_days"] * 30 / window_days)) if len(ai_users) else 0
    s.tile("Median active AI days / month", f"{median_days_month:.1f}", "per active Gemini user")
    s.tile("Avg use-case buckets per AI user", f"{per_user_buckets.mean():.1f}" if len(per_user_buckets) else "0")
    s.tile("Classified actions", f"{pct((ev['use_case'] != 'Unclassified').sum(), len(ev))}%")
    by_bucket = (ev.groupby("use_case").agg(actions=("event_name", "size"), users=("user_email", "nunique"))
                 .reindex(BUCKETS + ["Unclassified"]).fillna(0).astype(int))
    by_bucket["share_pct"] = (100 * by_bucket["actions"] / max(len(ev), 1)).round(1)
    by_bucket["pct_of_ai_users"] = (100 * by_bucket["users"] / max(len(ai_users), 1)).round(1)
    s.chart("Actions by use case", by_bucket["actions"][by_bucket["actions"] > 0].sort_values(ascending=False), "actions")
    s.table("Use-case adoption", by_bucket.reset_index().rename(columns={"use_case": "use_case"}),
            csv="gemini_use_cases", limit=20)
    fam = ev["family"].value_counts()
    s.chart("Share of actions by type", (100 * fam / fam.sum()).round(1), "%")
    cat = ev.assign(cat=ev["other_params"].apply(_event_category)).groupby("cat").size()
    if len(cat):
        s.chart("Event category (active vs proactive)", cat.sort_values(ascending=False), "actions")
    per_user = (ev.pivot_table(index="user_email", columns="use_case", values="event_name",
                               aggfunc="size", fill_value=0))
    per_user.insert(0, "buckets_used", per_user.gt(0).sum(axis=1))
    s.table("Use cases per user", per_user.reset_index().sort_values("buckets_used", ascending=False),
            csv="gemini_use_cases_by_user", limit=10)
    unmapped = ev[ev["use_case"] == "Unclassified"].groupby(["app", "feature", "action"]).size()
    if len(unmapped):
        s.table("Unclassified action values (map them in config.yaml)",
                unmapped.rename("actions").reset_index().sort_values("actions", ascending=False),
                csv="gemini_unclassified", limit=15)
    raw_actions = ev.groupby(["app", "feature", "action", "use_case"]).size().rename("actions").reset_index()
    s.table("Every app / feature / action combination seen", raw_actions.sort_values("actions", ascending=False),
            csv="gemini_action_catalog", limit=15)
    limits = ev[ev["action"].str.contains("limit|quota|exceed", case=False, na=False)]
    s.notes.append("Users hitting AI usage limits: " + (
        f"{limits['user_email'].nunique()} users" if len(limits) else
        "no limit/quota events appear in the Gemini audit log (not exposed by Google)."))
    return s


def _event_category(other: str) -> str:
    import json
    try:
        return json.loads(other).get("event_category", "not reported") if other else "not reported"
    except Exception:
        return "not reported"


def time_section(events: pd.DataFrame, cfg: dict) -> Section:
    s = Section("gemini_time", "When people use Gemini",
                intro=f"Times in {cfg['timezone']}. Working hours: "
                      f"{cfg['work_hours']['start']}:00–{cfg['work_hours']['end']}:00 on configured workdays.")
    if events.empty:
        s.status, s.reason = "empty", "No Gemini events in the window."
        return s
    s.heatmaps.append({"title": "Gemini actions by weekday and hour", "grid": heatmap(events["timestamp"], cfg["timezone"])})
    ah = after_hours_mask(events["timestamp"], cfg["timezone"], cfg["work_hours"])
    s.tile("Gemini actions outside working hours", f"{pct(ah.sum(), len(events))}%")
    daily = events.groupby(events["timestamp"].dt.tz_convert(cfg["timezone"]).dt.date).agg(
        actions=("event_name", "size"), active_users=("user_email", "nunique"))
    s.sparks.append({"title": "Daily trend", "series": [
        ("Actions / day", daily["actions"].tolist(), int(daily["actions"].iloc[-1])),
        ("Active users / day", daily["active_users"].tolist(), int(daily["active_users"].iloc[-1]))]})
    s.table("Daily Gemini activity", daily.reset_index().rename(columns={"timestamp": "date"}),
            csv="gemini_daily", limit=0)
    periods = cfg.get("periods") or []
    if periods:
        rows = []
        for per in periods:
            a, b = pd.Timestamp(per["start"], tz=cfg["timezone"]), pd.Timestamp(per["end"], tz=cfg["timezone"]) + pd.Timedelta(days=1)
            sub = events[(events["timestamp"] >= a) & (events["timestamp"] < b)]
            days = max((b - a).days, 1)
            rows.append({"period": per.get("name", f"{per['start']}–{per['end']}"), "start": per["start"],
                         "end": per["end"], "actions": len(sub), "active_users": sub["user_email"].nunique(),
                         "actions_per_day": round(len(sub) / days, 1)})
        s.table("Period comparison (e.g. term vs holiday, before vs after)", pd.DataFrame(rows), csv="gemini_periods")
    else:
        s.notes.append("Add `periods` to config.yaml (name, start, end) to compare term vs holiday or before vs after a change.")
    return s


def group_dimensions(users: pd.DataFrame, dims: list[str]) -> dict[str, pd.Series]:
    out = {}
    for d in dims:
        if d == "ou_path":
            out[d] = users["ou_path"]
        elif d.startswith("ou_level_"):
            n = int(d.rsplit("_", 1)[1])
            out[d] = users["ou_path"].apply(lambda p: "/" + "/".join([x for x in str(p).split("/") if x][:n]))
        elif d.startswith("custom:") and d[7:] in users.columns:
            out[d] = users[d[7:]].fillna("(blank)").astype(str)
    return out


def groups_section(summary: pd.DataFrame, users: pd.DataFrame, cfg: dict) -> Section:
    s = Section("groups", "Adoption by group",
                intro=f"Groups smaller than {cfg['min_group_size']} people are hidden. Target reach: "
                      f"{cfg['target_adoption_pct']}% of users with at least one Gemini action.")
    df = summary.merge(users.drop(columns=[c for c in ("name", "ou_path", "suspended") if c in users.columns]),
                       on="user_email", how="left")
    dims = group_dimensions(df, cfg.get("group_by") or ["ou_path"])
    minsize, target = int(cfg["min_group_size"]), float(cfg["target_adoption_pct"])
    for dim, key in dims.items():
        rows, champs = [], []
        for g, grp in df.groupby(key):
            n = len(grp)
            active = int((grp["total_actions"] > 0).sum())
            row = {"group": g, "users": n}
            if n < minsize:
                row.update({"active_users": "hidden", "adoption_pct": "hidden", "status": f"<{minsize} users"})
            else:
                ap = pct(active, n)
                row.update({
                    "active_users": active, "adoption_pct": ap,
                    "avg_actions_per_user": round(grp["total_actions"].mean(), 1),
                    "median_active_days": float(grp.loc[grp["total_actions"] > 0, "active_days"].median() or 0),
                    **{f"tier_{t.lower()}": int((grp["tier"] == t).sum()) for t in ("High", "Medium", "Low", "Zero")},
                    "status": "below target" if ap < target else "on target"})
                cut = grp["total_actions"].quantile(0.9)
                top = grp[(grp["total_actions"] >= cut) & (grp["total_actions"] > 0)]
                champs += [{"dimension": dim, "group": g, "user_email": u, "total_actions": t}
                           for u, t in zip(top["user_email"], top["total_actions"])]
            rows.append(row)
        table = pd.DataFrame(rows)
        s.table(f"By {dim.replace('_', ' ')}", table, csv=f"groups_{dim.replace(':', '_').replace('.', '_')}", limit=25)
        if champs:
            s.table(f"Top 10% of users in each group ({dim})", pd.DataFrame(champs),
                    csv=f"champions_{dim.replace(':', '_').replace('.', '_')}", limit=15)
        below = table[table.get("status", pd.Series(dtype=str)) == "below target"] if len(table) else table
        if dim == next(iter(dims)):
            s.tile(f"Groups below {target:.0f}% target", f"{len(below)} of {len(table)}", dim)
    if not dims:
        s.status, s.reason = "empty", "No group_by dimensions configured."
    champions = df[df["tier"] == "High"]
    s.tile("Champions (High tier)", f"{len(champions)}")
    s.tile("Non-users (Zero tier)", f"{int((df['tier'] == 'Zero').sum())}")
    return s
