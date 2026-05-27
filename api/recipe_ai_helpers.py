"""Shared helpers for AI recipe generation, scaling, and feedback."""

import re
from typing import Any

from .models import Recipe, UserProfile


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


def suggestion_from_raw(item: dict) -> dict:
    macros = item.get("macros") or {}
    return {
        "recipe_title": item.get("recipe_title", "Untitled"),
        "prep_time_min": int(item.get("prep_time_min", 0)),
        "difficulty": str(item.get("difficulty", "medium")).lower(),
        "calories_kcal": int(item.get("calories_kcal", 0)),
        "protein_g": int(macros.get("protein_g", 0)),
        "carbs_g": int(macros.get("carbs_g", 0)),
        "fats_g": int(macros.get("fats_g", 0)),
        "ingredients_used": item.get("ingredients_used", []),
        "missing_ingredients": item.get("missing_ingredients", []),
        "instructions": item.get("instructions", []),
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
