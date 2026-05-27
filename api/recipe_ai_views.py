"""Additional AI recipe & meal-plan endpoints."""

import json
import logging
import re
from django.db.models import Q
from google import genai
from django.conf import settings
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AIGenerationLog, Ingredient, MealPlanEntry, Recipe
from .recipe_ai_helpers import (
    append_feedback,
    normalize_difficulty,
    scale_recipe_fields,
)
from .serializers import MealPlanEntrySerializer
from .views import (
    FREE_DAILY_GENERATION_LIMIT,
    GenerateRecipeView,
    _get_profile,
    _log_generation,
    _pick_serializer_for_recipe,
    _user_is_premium,
)

logger = logging.getLogger(__name__)


class SuggestionFeedbackView(APIView):
    """POST — record like/dislike on a suggestion for future prompts."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        action = request.data.get("action")
        suggestion = request.data.get("suggestion")
        if action not in ("like", "dislike") or not isinstance(suggestion, dict):
            return Response(
                {"detail": "action (like|dislike) and suggestion object required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        profile = _get_profile(request.user)
        append_feedback(profile, action, suggestion)
        return Response({"ok": True})


class RecipeScaleView(APIView):
    """POST — scale recipe ingredients and macros to a new serving count."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, recipe_id):
        servings = request.data.get("servings")
        try:
            servings = int(servings)
        except (TypeError, ValueError):
            return Response(
                {"detail": "servings must be a positive integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if servings < 1 or servings > 24:
            return Response(
                {"detail": "servings must be between 1 and 24."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        recipe = (
            Recipe.objects.filter(id=recipe_id)
            .filter(
                Q(created_by=request.user) | Q(savedrecipe__user=request.user),
            )
            .distinct()
            .first()
        )
        if not recipe:
            return Response(status=status.HTTP_404_NOT_FOUND)

        patch = scale_recipe_fields(recipe, servings)
        for field, value in patch.items():
            setattr(recipe, field, value)
        recipe.save(update_fields=list(patch.keys()))

        serializer_cls = _pick_serializer_for_recipe(request.user)
        return Response(
            serializer_cls(recipe, context={"request": request}).data,
        )


class MealPlanNutritionSummaryView(APIView):
    """GET — aggregate macros for meal-plan entries with linked recipes."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        start = request.query_params.get("start")
        end = request.query_params.get("end")
        if not start or not end:
            return Response(
                {"detail": "start and end query params required (YYYY-MM-DD)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        entries = MealPlanEntry.objects.filter(
            user=request.user,
            date__gte=start,
            date__lte=end,
            recipe__isnull=False,
        ).select_related("recipe")

        totals = {
            "calories": 0,
            "protein_g": 0,
            "carbs_g": 0,
            "fats_g": 0,
            "meals_planned": 0,
        }
        for entry in entries:
            r = entry.recipe
            if not r:
                continue
            totals["meals_planned"] += 1
            totals["calories"] += r.total_calories or 0
            totals["protein_g"] += r.protein_g or 0
            totals["carbs_g"] += r.carbs_g or 0
            totals["fats_g"] += r.fats_g or 0

        return Response(totals)


class FillMealPlanAIView(APIView):
    """POST — AI fills empty dinner slots for the current week."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if not _user_is_premium(request.user):
            today = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
            fill_count = AIGenerationLog.objects.filter(
                user=request.user,
                mode="meal_plan_fill",
                created_at__gte=today,
            ).count()
            if fill_count >= 1:
                return Response(
                    {
                        "error": (
                            "Free users can auto-fill the meal plan once per day. "
                            "Upgrade to Premium for unlimited access."
                        )
                    },
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )

        start = request.data.get("start")
        end = request.data.get("end")
        if not start or not end:
            return Response(
                {"detail": "start and end dates required (YYYY-MM-DD)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile = _get_profile(request.user)
        ingredients_qs = Ingredient.objects.filter(user=request.user)
        ingredient_names = [i.name for i in ingredients_qs.order_by("expiration_date")]
        equipment = profile.equipment or []
        lang_label = "Turkish" if profile.language == "tr" else "English"

        system_instruction = (
            "Act as a meal-planning chef API. "
            f"Respond ONLY in {lang_label}. "
            f"User diet: {profile.get_diet_type_display()}. "
            f"Portions per meal: {profile.default_portions}. "
            f"Pantry: {', '.join(ingredient_names) or 'none'}. "
            f"Equipment: {', '.join(equipment) or 'standard'}. "
            f"Cuisine preference: {profile.hometown or 'any'}. "
            "For each day from start to end date, suggest ONE dinner recipe "
            "using pantry items (prioritize expiring). "
            "Return ONLY raw JSON: "
            '{"days":[{"date":"YYYY-MM-DD","recipe":{'
            '"recipe_title":"","prep_time_min":0,"difficulty":"medium",'
            '"calories_kcal":0,"macros":{"protein_g":0,"carbs_g":0,"fats_g":0},'
            '"ingredients_used":[],"missing_ingredients":[],"instructions":[]'
            "}}]}"
        )

        user_message = f"Fill dinner slots from {start} to {end}."

        try:
            client = genai.Client(api_key=settings.GEMINI_API_KEY)
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=user_message,
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                ),
            )
            raw = response.text.strip()
            cleaned = re.sub(r"```(?:json)?", "", raw).strip()
            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError:
                s, e = cleaned.find("{"), cleaned.rfind("}")
                if s >= 0 and e > s:
                    data = json.loads(cleaned[s : e + 1])
                else:
                    raise
        except json.JSONDecodeError:
            logger.exception("Meal plan fill AI returned invalid JSON")
            return Response(
                {"error": "AI returned an invalid response."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception:
            logger.exception("Meal plan fill AI failed")
            return Response(
                {"error": "AI service is temporarily unavailable."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        days_raw = data.get("days", []) if isinstance(data, dict) else []
        created_entries = []
        user = request.user

        for day_item in days_raw:
            if not isinstance(day_item, dict):
                continue
            date_str = day_item.get("date")
            recipe_data = day_item.get("recipe")
            if not date_str or not isinstance(recipe_data, dict):
                continue

            if MealPlanEntry.objects.filter(
                user=user,
                date=date_str,
                meal_slot=MealPlanEntry.MealSlot.DINNER,
            ).exists():
                continue

            ai_shape = {
                "recipe_title": recipe_data.get("recipe_title", "Meal"),
                "prep_time_min": recipe_data.get("prep_time_min", 30),
                "difficulty": normalize_difficulty(
                    recipe_data.get("difficulty", "medium"),
                ),
                "calories_kcal": recipe_data.get("calories_kcal", 0),
                "macros": recipe_data.get("macros", {}),
                "ingredients_used": recipe_data.get("ingredients_used", []),
                "missing_ingredients": recipe_data.get("missing_ingredients", []),
                "instructions": recipe_data.get("instructions", []),
            }
            recipe = GenerateRecipeView._save_recipe(user, ai_shape)
            recipe.servings = profile.default_portions
            recipe.save(update_fields=["servings"])

            entry = MealPlanEntry.objects.create(
                user=user,
                date=date_str,
                meal_slot=MealPlanEntry.MealSlot.DINNER,
                recipe=recipe,
            )
            created_entries.append(entry)

        AIGenerationLog.objects.create(user=user, mode="meal_plan_fill")

        return Response(
            {
                "created_count": len(created_entries),
                "entries": MealPlanEntrySerializer(
                    created_entries, many=True,
                ).data,
            },
            status=status.HTTP_201_CREATED,
        )
