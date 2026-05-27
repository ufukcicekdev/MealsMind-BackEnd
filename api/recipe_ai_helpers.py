"""Shared helpers for AI recipe generation, scaling, and feedback."""

import json
import re
from typing import Any

from .models import Recipe, UserProfile

DIFFICULTY_ALIASES = {
    "easy": "easy",
    "kolay": "easy",
    "medium": "medium",
    "orta": "medium",
    "moderate": "medium",
    "hard": "hard",
    "zor": "hard",
    "difficult": "hard",
}


def normalize_difficulty(raw: Any) -> str:
    key = str(raw or "medium").lower().strip()
    return DIFFICULTY_ALIASES.get(key, "medium")


def normalize_ingredient_item(item: Any) -> str:
    if item is None:
        return ""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        name = str(
            item.get("name")
            or item.get("ingredient")
            or item.get("item")
            or item.get("title")
            or "",
        ).strip()
        qty = str(
            item.get("quantity")
            or item.get("amount")
            or item.get("qty")
            or "",
        ).strip()
        if name and qty:
            return f"{qty} {name}"
        return name or qty
    return str(item).strip()


def normalize_ingredient_list(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for item in items:
        text = normalize_ingredient_item(item)
        if text:
            out.append(text)
    return out


def normalize_instructions(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict):
            step = item.get("step") or item.get("text") or item.get("instruction")
            if step:
                out.append(str(step).strip())
    return out


def feedback_prompt_suffix(profile: UserProfile) -> str:
    fb = profile.ai_recipe_feedback if isinstance(profile.ai_recipe_feedback, dict) else {}
    disliked = fb.get("disliked", [])
    if not isinstance(disliked, list) or not disliked:
        return ""
    lines = []
    for item in disliked[-12:]:
        if not isinstance(item, dict):
            continue
        title = item.get("title", "")
        ings = ", ".join((item.get("ingredients") or [])[:6])
        if title:
            lines.append(f"- {title}" + (f" (ingredients: {ings})" if ings else ""))
    if not lines:
        return ""
    return (
        " The user previously disliked these suggestions — avoid similar dishes, "
        "flavors, or main ingredients:\n" + "\n".join(lines)
    )


def append_feedback(profile: UserProfile, action: str, suggestion: dict) -> None:
    fb = profile.ai_recipe_feedback if isinstance(profile.ai_recipe_feedback, dict) else {}
    key = "liked" if action == "like" else "disliked"
    bucket = list(fb.get(key, []))
    entry = {
        "title": suggestion.get("recipe_title", ""),
        "ingredients": suggestion.get("ingredients_used", [])[:12],
    }
    bucket = [b for b in bucket if b.get("title") != entry["title"]]
    bucket.append(entry)
    fb[key] = bucket[-20:]
    profile.ai_recipe_feedback = fb
    profile.save(update_fields=["ai_recipe_feedback"])


def _matching_brace_end(text: str, start: int) -> int | None:
    depth = 0
    in_string = False
    escape = False
    for j in range(start, len(text)):
        ch = text[j]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return j
    return None


def _extract_recipe_dicts(text: str) -> list[dict]:
    """Pull recipe objects from broken JSON (e.g. strings mixed into recipes array)."""
    results: list[dict] = []
    search_from = 0
    marker = '"recipe_title"'
    while True:
        idx = text.find(marker, search_from)
        if idx == -1:
            break
        start = text.rfind("{", 0, idx)
        if start == -1:
            search_from = idx + len(marker)
            continue
        end = _matching_brace_end(text, start)
        if end is None:
            search_from = idx + len(marker)
            continue
        chunk = text[start : end + 1]
        try:
            obj = json.loads(chunk)
            if isinstance(obj, dict) and obj.get("recipe_title"):
                results.append(obj)
        except json.JSONDecodeError:
            pass
        search_from = end + 1
    return results


def _normalize_recipes_payload(data: Any) -> dict:
    if isinstance(data, list):
        return {"recipes": [x for x in data if isinstance(x, dict) and x.get("recipe_title")]}
    if not isinstance(data, dict):
        return {"recipes": []}
    recipes = data.get("recipes")
    if isinstance(recipes, list):
        return {
            "recipes": [
                x for x in recipes if isinstance(x, dict) and x.get("recipe_title")
            ],
        }
    if data.get("recipe_title"):
        return {"recipes": [data]}
    return {"recipes": []}


def parse_ai_recipes_payload(raw_text: str) -> dict:
    """
    Parse Gemini recipe JSON; recover individual recipe objects when the
    top-level JSON is malformed.
    """
    cleaned = raw_text.strip()
    cleaned = re.sub(r"```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()

    candidates = [cleaned]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start >= 0 and end > start:
        candidates.append(cleaned[start : end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
            normalized = _normalize_recipes_payload(data)
            if normalized["recipes"]:
                return normalized
        except json.JSONDecodeError:
            continue

    extracted = _extract_recipe_dicts(cleaned)
    if extracted:
        return {"recipes": extracted}

    raise json.JSONDecodeError("Could not parse AI recipe JSON", cleaned, 0)


def suggestion_from_raw(item: dict) -> dict:
    macros = item.get("macros") or {}
    return {
        "recipe_title": item.get("recipe_title", "Untitled"),
        "prep_time_min": int(item.get("prep_time_min", 0)),
        "difficulty": normalize_difficulty(item.get("difficulty", "medium")),
        "calories_kcal": int(item.get("calories_kcal", 0)),
        "protein_g": int(macros.get("protein_g", 0)),
        "carbs_g": int(macros.get("carbs_g", 0)),
        "fats_g": int(macros.get("fats_g", 0)),
        "ingredients_used": normalize_ingredient_list(item.get("ingredients_used", [])),
        "missing_ingredients": normalize_ingredient_list(
            item.get("missing_ingredients", []),
        ),
        "instructions": normalize_instructions(item.get("instructions", [])),
    }


def scale_ingredient_line(line: str, factor: float) -> str:
    if factor == 1.0 or not line:
        return line

    def repl(match: re.Match) -> str:
        raw = match.group(1).replace(",", ".")
        try:
            val = float(raw)
        except ValueError:
            return match.group(0)
        new_val = val * factor
        if abs(new_val - round(new_val)) < 0.05:
            text = str(int(round(new_val)))
        else:
            text = f"{new_val:.1f}".rstrip("0").rstrip(".")
        return text + match.group(2)

    scaled, count = re.subn(
        r"^(\d+(?:[.,]\d+)?)(\s*)",
        repl,
        line.strip(),
        count=1,
    )
    return scaled if count else line


def scale_recipe_fields(recipe: Recipe, target_servings: int) -> dict[str, Any]:
    base = recipe.servings or 4
    if target_servings < 1:
        target_servings = 1
    factor = target_servings / base

    return {
        "servings": target_servings,
        "total_calories": max(0, int(round(recipe.total_calories * factor))),
        "protein_g": max(0, int(round(recipe.protein_g * factor))),
        "carbs_g": max(0, int(round(recipe.carbs_g * factor))),
        "fats_g": max(0, int(round(recipe.fats_g * factor))),
        "ingredients_used": [
            scale_ingredient_line(x, factor) for x in (recipe.ingredients_used or [])
        ],
        "missing_ingredients": [
            scale_ingredient_line(x, factor) for x in (recipe.missing_ingredients or [])
        ],
    }
