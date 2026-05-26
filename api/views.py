import base64
import json
import logging
import os
import re
import uuid

from google import genai
from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from rest_framework import generics, permissions, status, viewsets
from rest_framework.exceptions import Throttled
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Ingredient, Like, Recipe, RecipeCategory, SavedRecipe, UserProfile
from .serializers import (
    CommunityShareSerializer,
    GenerateRecipeRequestSerializer,
    IngredientSerializer,
    LikeSerializer,
    RecipeCategorySerializer,
    RecipeFreeSerializer,
    RecipeSerializer,
    UserProfileSerializer,
)

logger = logging.getLogger(__name__)

FREE_DAILY_GENERATION_LIMIT = 3
FREE_MAX_INGREDIENTS = 5


# ======================================================================== #
#  Helpers / permissions
# ======================================================================== #

class IsPremiumUser(permissions.BasePermission):
    message = "This action is restricted to Premium subscribers."

    def has_permission(self, request, view):
        profile = getattr(request.user, "profile", None)
        return profile is not None and profile.is_premium


def _get_profile(user) -> UserProfile:
    """Return user profile, creating a default one on first access."""
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def _user_is_premium(user) -> bool:
    return _get_profile(user).is_premium


def _generation_count_today(user) -> int:
    start_of_day = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return Recipe.objects.filter(
        created_by=user,
        is_ai_generated=True,
        created_at__gte=start_of_day,
    ).count()


def _pick_serializer_for_recipe(user):
    """Return the full or restricted serializer based on subscription."""
    if _user_is_premium(user):
        return RecipeSerializer
    return RecipeFreeSerializer


# ======================================================================== #
#  UserProfile
# ======================================================================== #

class UserProfileView(generics.RetrieveUpdateAPIView):
    """GET / PATCH the authenticated user's profile."""

    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return _get_profile(self.request.user)


# ======================================================================== #
#  Ingredient (pantry CRUD)
# ======================================================================== #

class IngredientViewSet(viewsets.ModelViewSet):
    """
    Full CRUD for the user's pantry.
    Free users are capped at 5 ingredients (enforced in serializer + here).
    """

    serializer_class = IngredientSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Ingredient.objects.filter(user=self.request.user)

    @transaction.atomic
    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


# ======================================================================== #
#  AI Recipe Generation  –  POST /api/recipes/generate/
# ======================================================================== #

class GenerateRecipeView(APIView):
    """
    Accept a POST with optional equipment/extra_prompt, call the Gemini API,
    persist the recipe, and return it to the client.
    """

    permission_classes = [permissions.IsAuthenticated]

    # ------------------------------------------------------------------ #
    #  paywall gate
    # ------------------------------------------------------------------ #
    def _enforce_free_limits(self, user):
        if _user_is_premium(user):
            return

        today_count = _generation_count_today(user)
        if today_count >= FREE_DAILY_GENERATION_LIMIT:
            raise Throttled(
                detail=(
                    f"Free users are limited to {FREE_DAILY_GENERATION_LIMIT} AI "
                    "recipe generations per day. Upgrade to Premium for unlimited access."
                )
            )

    # ------------------------------------------------------------------ #
    #  Gemini prompt builder
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_prompt(profile, ingredients_qs, equipment, extra_prompt):
        expiring_first = ingredients_qs.order_by("expiration_date")
        ingredient_names = [i.name for i in expiring_first]

        lang_label = "Turkish" if profile.language == "tr" else "English"
        ingredients_str = ", ".join(ingredient_names) if ingredient_names else "none provided"
        equipment_str = ", ".join(equipment) if equipment else "standard kitchen"

        cuisine_pref = profile.hometown or ""
        cuisine_instruction = ""
        if cuisine_pref:
            cuisine_instruction = (
                f"The user prefers {cuisine_pref} cuisine. Incorporate flavors, "
                f"techniques, and ingredients typical of {cuisine_pref} cooking. "
            )

        system_instruction = (
            "Act as an anti-waste expert chef API. "
            "Your primary goal is to MINIMIZE food waste by prioritizing ingredients "
            "closest to their expiration date. "
            f"Respond ONLY in {lang_label}. "
            f"Translate ALL string values (recipe title, difficulty, instructions) "
            f"strictly to {lang_label}. "
            f"The user follows a {profile.get_diet_type_display()} diet. "
            f"{cuisine_instruction}"
            f"Available ingredients (sorted by expiry — use soonest-expiring FIRST): "
            f"{ingredients_str}. "
            f"Available kitchen equipment: {equipment_str}. "
            f"Number of portions: {profile.default_portions}. "
            "Generate a recipe that uses as many expiring ingredients as possible. "
            "Return ONLY a raw JSON string — no markdown, no code fences "
            "(```json), no explanation before or after. "
            "The JSON MUST match this exact structure: "
            '{"recipe_title": "", "prep_time_min": 0, "difficulty": "", '
            '"calories_kcal": 0, '
            '"macros": {"protein_g": 0, "carbs_g": 0, "fats_g": 0}, '
            '"ingredients_used": [], "missing_ingredients": [], '
            '"instructions": []}'
        )

        user_message = "Generate a recipe."
        if extra_prompt:
            user_message += f" Additional request: {extra_prompt}"

        return system_instruction, user_message

    # ------------------------------------------------------------------ #
    #  Gemini call
    # ------------------------------------------------------------------ #
    @staticmethod
    def _call_gemini(system_instruction, user_message):
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_message,
            config=genai.types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
            ),
        )
        return response.text

    # ------------------------------------------------------------------ #
    #  Parse & sanitise AI output
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_ai_response(raw_text):
        cleaned = raw_text.strip()
        cleaned = re.sub(r"```(?:json)?", "", cleaned).strip()
        return json.loads(cleaned)

    # ------------------------------------------------------------------ #
    #  Persist recipe
    # ------------------------------------------------------------------ #
    @staticmethod
    def _guess_category(title, ingredients_used):
        """Best-effort category assignment based on title/ingredient keywords."""
        text = (title + " " + " ".join(ingredients_used)).lower()
        keyword_map = {
            "breakfast": ["kahvaltı", "breakfast", "omlet", "omelette", "egg", "yumurta", "pancake"],
            "soup": ["çorba", "soup"],
            "salad": ["salata", "salad"],
            "main-course": ["tavuk", "chicken", "et", "beef", "meat", "fish", "balık", "köfte", "steak", "kebab"],
            "pasta": ["makarna", "pasta", "spaghetti", "noodle", "lazanya", "lasagna"],
            "dessert": ["tatlı", "dessert", "cake", "cookie", "baklava", "brownie", "pudding", "cheesecake"],
            "snack": ["atıştırmalık", "snack", "dip", "toast", "tost", "wrap"],
            "drink": ["içecek", "drink", "smoothie", "juice", "shake"],
        }
        for slug, keywords in keyword_map.items():
            if any(kw in text for kw in keywords):
                return RecipeCategory.objects.filter(slug=slug).first()
        return RecipeCategory.objects.filter(slug="other").first()

    @staticmethod
    def _save_recipe(user, data):
        difficulty_map = {
            "easy": Recipe.Difficulty.EASY,
            "medium": Recipe.Difficulty.MEDIUM,
            "hard": Recipe.Difficulty.HARD,
        }
        raw_difficulty = str(data.get("difficulty", "medium")).lower()
        ingredients_used = data.get("ingredients_used", [])
        missing_ingredients = data.get("missing_ingredients", [])

        category = GenerateRecipeView._guess_category(
            data.get("recipe_title", ""), ingredients_used,
        )

        recipe = Recipe.objects.create(
            title=data.get("recipe_title", "Untitled"),
            prep_time_min=int(data.get("prep_time_min", 0)),
            difficulty=difficulty_map.get(raw_difficulty, Recipe.Difficulty.MEDIUM),
            category=category,
            total_calories=int(data.get("calories_kcal", 0)),
            protein_g=int(data.get("macros", {}).get("protein_g", 0)),
            carbs_g=int(data.get("macros", {}).get("carbs_g", 0)),
            fats_g=int(data.get("macros", {}).get("fats_g", 0)),
            ingredients_used=ingredients_used,
            missing_ingredients=missing_ingredients,
            instructions=data.get("instructions", []),
            is_ai_generated=True,
            created_by=user,
        )

        SavedRecipe.objects.get_or_create(user=user, recipe=recipe)

        return recipe

    # ------------------------------------------------------------------ #
    #  Generate recipe image (premium only)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _generate_image(recipe_title, ingredients, lang_code):
        """Generate a food photo via Gemini Imagen and upload to S3."""
        try:
            prompt = (
                f"A professional, appetizing food photography of \"{recipe_title}\". "
                f"Key ingredients: {', '.join(ingredients[:6])}. "
                "Overhead shot on a clean white plate, soft natural lighting, "
                "shallow depth of field, restaurant-quality presentation. "
                "No text, no watermarks, no logos."
            )

            client = genai.Client(api_key=settings.GEMINI_API_KEY)
            response = client.models.generate_images(
                model="imagen-3.0-generate-002",
                prompt=prompt,
                config=genai.types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio="4:3",
                ),
            )

            if not response.generated_images:
                return ""

            image_data = response.generated_images[0].image.image_bytes
            filename = f"recipes/{uuid.uuid4().hex}.png"

            from django.core.files.storage import default_storage
            from django.core.files.base import ContentFile

            saved_name = default_storage.save(filename, ContentFile(image_data))
            return default_storage.url(saved_name)
        except Exception:
            logger.exception("Image generation failed")
            return ""

    # ------------------------------------------------------------------ #
    #  POST handler
    # ------------------------------------------------------------------ #
    def post(self, request):
        self._enforce_free_limits(request.user)

        payload_ser = GenerateRecipeRequestSerializer(data=request.data)
        payload_ser.is_valid(raise_exception=True)

        profile = _get_profile(request.user)
        ingredients_qs = Ingredient.objects.filter(user=request.user)
        equipment = payload_ser.validated_data.get("equipment", [])
        extra_prompt = payload_ser.validated_data.get("extra_prompt", "")

        system_instruction, user_message = self._build_prompt(
            profile, ingredients_qs, equipment, extra_prompt,
        )

        try:
            raw_text = self._call_gemini(system_instruction, user_message)
        except Exception:
            logger.exception("Gemini API call failed")
            return Response(
                {"error": "AI service is temporarily unavailable. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            ai_data = self._parse_ai_response(raw_text)
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.error("Gemini returned unparseable JSON: %s", raw_text[:500])
            return Response(
                {"error": "AI returned an invalid response. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        try:
            recipe = self._save_recipe(request.user, ai_data)
        except (TypeError, ValueError, KeyError):
            logger.exception("Failed to persist AI recipe from data: %s", ai_data)
            return Response(
                {"error": "AI returned an invalid response. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        if _user_is_premium(request.user):
            image_url = self._generate_image(
                recipe.title,
                ai_data.get("ingredients_used", []),
                profile.language,
            )
            if image_url:
                recipe.image_url = image_url
                recipe.save(update_fields=["image_url"])

        recipe.like_count = 0
        serializer_cls = _pick_serializer_for_recipe(request.user)
        output = serializer_cls(recipe, context={"request": request}).data

        return Response(output, status=status.HTTP_201_CREATED)


# ======================================================================== #
#  Community recipes  –  GET /api/community/recipes/
# ======================================================================== #

class CommunityRecipeListView(generics.ListAPIView):
    """
    GET — premium users browse public shared recipes.
    Free users are fully blocked (403).
    Supports ?category=slug filter and ?sort=popular|recent.
    """

    permission_classes = [permissions.IsAuthenticated, IsPremiumUser]
    pagination_class = None

    def get_serializer_class(self):
        return RecipeSerializer

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx

    def get_queryset(self):
        qs = (
            Recipe.objects.filter(is_public=True)
            .annotate(like_count=Count("likes"))
            .select_related("created_by", "category")
        )
        cat = self.request.query_params.get("category")
        if cat:
            qs = qs.filter(category__slug=cat)

        sort = self.request.query_params.get("sort", "recent")
        if sort == "popular":
            qs = qs.order_by("-like_count", "-created_at")
        else:
            qs = qs.order_by("-created_at")
        return qs


class CommunityCategoryStatsView(APIView):
    """GET — category list with public recipe counts."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        cats = RecipeCategory.objects.all().order_by("order")
        total = Recipe.objects.filter(is_public=True).count()

        result = [{"slug": "all", "name_tr": "Tümü", "name_en": "All",
                    "icon": "grid-outline", "count": total}]

        for cat in cats:
            count = Recipe.objects.filter(is_public=True, category=cat).count()
            if count > 0 or cat.slug == "other":
                result.append({
                    "slug": cat.slug,
                    "name_tr": cat.name_tr,
                    "name_en": cat.name_en,
                    "icon": cat.icon,
                    "count": count,
                })

        return Response(result)


# ======================================================================== #
#  Community share  –  POST /api/community/recipes/share/
# ======================================================================== #

class CommunityShareView(APIView):
    """POST — share an existing saved recipe to the community or create new one."""

    permission_classes = [permissions.IsAuthenticated, IsPremiumUser]

    def post(self, request):
        recipe_id = request.data.get("recipe_id")
        if recipe_id:
            try:
                recipe = Recipe.objects.get(pk=recipe_id, created_by=request.user)
            except Recipe.DoesNotExist:
                return Response({"error": "Recipe not found."}, status=status.HTTP_404_NOT_FOUND)
            recipe.is_public = True
            recipe.save(update_fields=["is_public"])
            recipe.like_count = recipe.likes.count()
            return Response(
                RecipeSerializer(recipe, context={"request": request}).data,
                status=status.HTTP_200_OK,
            )

        ser = CommunityShareSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        recipe = ser.save(created_by=request.user, is_ai_generated=False, is_public=True)
        recipe.like_count = 0
        return Response(
            RecipeSerializer(recipe, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


# ======================================================================== #
#  Like / unlike toggle
# ======================================================================== #

class LikeToggleView(APIView):
    """POST to toggle a like on a recipe (idempotent create/delete)."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, recipe_id):
        try:
            recipe = Recipe.objects.get(pk=recipe_id)
        except Recipe.DoesNotExist:
            return Response(
                {"error": "Recipe not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        like, created = Like.objects.get_or_create(
            user=request.user, recipe=recipe,
        )
        if not created:
            like.delete()
            new_count = recipe.likes.count()
            return Response({"liked": False, "like_count": new_count})

        new_count = recipe.likes.count()
        return Response(
            {"liked": True, "like_count": new_count},
            status=status.HTTP_201_CREATED,
        )


# ======================================================================== #
#  Recipe Categories  –  GET /api/categories/
# ======================================================================== #

class RecipeCategoryListView(generics.ListAPIView):
    """GET — list all recipe categories (no pagination)."""

    serializer_class = RecipeCategorySerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None
    queryset = RecipeCategory.objects.all()


# ======================================================================== #
#  My Recipes (saved)  –  GET /api/recipes/saved/
# ======================================================================== #

class MyRecipesView(generics.ListAPIView):
    """GET — list current user's saved recipes with optional category filter."""

    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        return _pick_serializer_for_recipe(self.request.user)

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx

    def get_queryset(self):
        saved_ids = SavedRecipe.objects.filter(
            user=self.request.user,
        ).values_list("recipe_id", flat=True)

        qs = (
            Recipe.objects.filter(id__in=saved_ids)
            .annotate(like_count=Count("likes"))
            .select_related("category")
        )
        cat = self.request.query_params.get("category")
        if cat:
            qs = qs.filter(category__slug=cat)
        return qs


# ======================================================================== #
#  Save / Unsave recipe  –  POST /api/recipes/<id>/save/
# ======================================================================== #

class SaveRecipeToggleView(APIView):
    """POST to toggle bookmark (save/unsave) a recipe."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, recipe_id):
        try:
            recipe = Recipe.objects.get(pk=recipe_id)
        except Recipe.DoesNotExist:
            return Response({"error": "Recipe not found."}, status=status.HTTP_404_NOT_FOUND)

        saved, created = SavedRecipe.objects.get_or_create(
            user=request.user, recipe=recipe,
        )
        if not created:
            saved.delete()
            return Response({"saved": False})
        return Response({"saved": True}, status=status.HTTP_201_CREATED)


# ======================================================================== #
#  Create Recipe from Photo  –  POST /api/recipes/create-from-photo/
# ======================================================================== #

class CreateRecipeFromPhotoView(APIView):
    """
    Accept a food photo, send it to Gemini Vision to extract a full recipe,
    return it as editable draft (not yet saved to DB).
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser]

    def post(self, request):
        image_file = request.FILES.get("image")
        if not image_file:
            return Response(
                {"error": "No image provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if image_file.size > 10 * 1024 * 1024:
            return Response(
                {"error": "Image too large. Max 10 MB."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        image_bytes = image_file.read()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        content_type = image_file.content_type or "image/jpeg"
        mime = content_type if content_type.startswith("image/") else "image/jpeg"

        profile = _get_profile(request.user)
        lang_label = "Turkish" if profile.language == "tr" else "English"
        title_hint = request.data.get("title", "")

        title_instruction = ""
        if title_hint:
            title_instruction = f'The user says this dish is called "{title_hint}". Use this as the recipe title. '

        system_instruction = (
            "You are an expert chef AI. Analyze the provided food photo and "
            "generate a complete recipe for the dish shown. "
            f"{title_instruction}"
            f"Respond ONLY in {lang_label}. "
            f"Translate ALL string values strictly to {lang_label}. "
            "Return ONLY a raw JSON object — no markdown, no code fences. "
            "The JSON MUST match this exact structure: "
            '{"recipe_title": "", "prep_time_min": 0, '
            '"difficulty": "easy|medium|hard", '
            '"category": "breakfast|soup|salad|main-course|pasta|dessert|snack|drink|other", '
            '"calories_kcal": 0, '
            '"macros": {"protein_g": 0, "carbs_g": 0, "fats_g": 0}, '
            '"ingredients_used": ["ingredient 1", "ingredient 2"], '
            '"instructions": ["step 1", "step 2"], '
            '"servings": 4}'
        )

        try:
            client = genai.Client(api_key=settings.GEMINI_API_KEY)
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    {
                        "role": "user",
                        "parts": [
                            {"text": "Generate a complete recipe for the dish in this photo."},
                            {
                                "inline_data": {
                                    "mime_type": mime,
                                    "data": image_b64,
                                },
                            },
                        ],
                    },
                ],
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                ),
            )
            raw_text = response.text
        except Exception:
            logger.exception("Gemini Vision API call failed for recipe creation")
            return Response(
                {"error": "AI service is temporarily unavailable. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            cleaned = raw_text.strip()
            cleaned = re.sub(r"```(?:json)?", "", cleaned).strip()
            ai_data = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.error("Gemini returned unparseable JSON: %s", raw_text[:500])
            return Response(
                {"error": "AI could not analyze the photo. Try a clearer image."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        categories = list(
            RecipeCategory.objects.values_list("slug", flat=True)
        )
        ai_cat_slug = ai_data.get("category", "other")
        if ai_cat_slug not in categories:
            ai_cat_slug = "other"

        draft = {
            "recipe_title": ai_data.get("recipe_title", ""),
            "prep_time_min": int(ai_data.get("prep_time_min", 0)),
            "difficulty": ai_data.get("difficulty", "medium"),
            "category_slug": ai_cat_slug,
            "calories_kcal": int(ai_data.get("calories_kcal", 0)),
            "protein_g": int(ai_data.get("macros", {}).get("protein_g", 0)),
            "carbs_g": int(ai_data.get("macros", {}).get("carbs_g", 0)),
            "fats_g": int(ai_data.get("macros", {}).get("fats_g", 0)),
            "ingredients_used": ai_data.get("ingredients_used", []),
            "instructions": ai_data.get("instructions", []),
            "servings": int(ai_data.get("servings", 4)),
        }

        return Response({"draft": draft}, status=status.HTTP_200_OK)


class SaveDraftRecipeView(APIView):
    """POST — save an edited recipe draft to the database."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        data = request.data
        difficulty_map = {
            "easy": Recipe.Difficulty.EASY,
            "medium": Recipe.Difficulty.MEDIUM,
            "hard": Recipe.Difficulty.HARD,
        }

        category = RecipeCategory.objects.filter(
            slug=data.get("category_slug", "other"),
        ).first()

        recipe = Recipe.objects.create(
            title=data.get("recipe_title", "Untitled"),
            prep_time_min=int(data.get("prep_time_min", 0)),
            difficulty=difficulty_map.get(
                str(data.get("difficulty", "medium")).lower(),
                Recipe.Difficulty.MEDIUM,
            ),
            category=category,
            total_calories=int(data.get("calories_kcal", 0)),
            protein_g=int(data.get("protein_g", 0)),
            carbs_g=int(data.get("carbs_g", 0)),
            fats_g=int(data.get("fats_g", 0)),
            ingredients_used=data.get("ingredients_used", []),
            instructions=data.get("instructions", []),
            image_url=data.get("image_url", ""),
            is_ai_generated=False,
            is_public=data.get("is_public", False),
            created_by=request.user,
        )

        SavedRecipe.objects.get_or_create(user=request.user, recipe=recipe)

        recipe.like_count = 0
        serializer = RecipeSerializer(recipe, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


# ======================================================================== #
#  Pantry Scan  –  POST /api/pantry/scan/
# ======================================================================== #

class PantryScanView(APIView):
    """
    Accept an image of a fridge/pantry, send it to Gemini Vision,
    and return a list of detected food items.
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser]

    def post(self, request):
        image_file = request.FILES.get("image")
        if not image_file:
            return Response(
                {"error": "No image provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        max_size = 10 * 1024 * 1024  # 10 MB
        if image_file.size > max_size:
            return Response(
                {"error": "Image too large. Max 10 MB."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        image_bytes = image_file.read()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        content_type = image_file.content_type or "image/jpeg"
        mime = content_type if content_type.startswith("image/") else "image/jpeg"

        profile = _get_profile(request.user)
        lang_label = "Turkish" if profile.language == "tr" else "English"

        system_instruction = (
            "You are a food recognition AI. Analyze the provided image of a "
            "fridge, pantry, or kitchen shelf. "
            "Identify all visible food items and ingredients. "
            f"Respond ONLY in {lang_label}. "
            "Return ONLY a raw JSON array of objects — no markdown, no code fences. "
            "Each object must have: "
            '{"name": "ingredient name", "category": "dairy|meat|vegetable|fruit|grain|beverage|condiment|snack|other"}. '
            "Be specific: say 'whole milk' not just 'milk', "
            "'cherry tomatoes' not just 'tomatoes'. "
            "If you cannot identify any food items, return an empty array: []"
        )

        try:
            client = genai.Client(api_key=settings.GEMINI_API_KEY)
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    {
                        "role": "user",
                        "parts": [
                            {"text": "Identify all food items in this image."},
                            {
                                "inline_data": {
                                    "mime_type": mime,
                                    "data": image_b64,
                                },
                            },
                        ],
                    },
                ],
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                ),
            )
            raw_text = response.text
        except Exception:
            logger.exception("Gemini Vision API call failed")
            return Response(
                {"error": "AI service is temporarily unavailable. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            cleaned = raw_text.strip()
            cleaned = re.sub(r"```(?:json)?", "", cleaned).strip()
            items = json.loads(cleaned)
            if not isinstance(items, list):
                items = []
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.error("Gemini Vision returned unparseable JSON: %s", raw_text[:500])
            return Response(
                {"error": "AI could not parse the image. Try a clearer photo."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response({"items": items}, status=status.HTTP_200_OK)


# ======================================================================== #
#  Pantry Voice  –  POST /api/pantry/voice/
# ======================================================================== #

class PantryVoiceView(APIView):
    """
    Accept an audio recording, send it to Gemini,
    and return a list of detected ingredient names.
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser]

    def post(self, request):
        audio_file = request.FILES.get("audio")
        if not audio_file:
            return Response(
                {"error": "No audio provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        max_size = 10 * 1024 * 1024
        if audio_file.size > max_size:
            return Response(
                {"error": "Audio too large. Max 10 MB."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        audio_bytes = audio_file.read()
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")

        content_type = audio_file.content_type or "audio/mp4"
        mime_map = {
            "audio/mp4": "audio/mp4",
            "audio/m4a": "audio/mp4",
            "audio/x-m4a": "audio/mp4",
            "audio/mpeg": "audio/mpeg",
            "audio/wav": "audio/wav",
            "audio/webm": "audio/webm",
            "audio/ogg": "audio/ogg",
        }
        mime = mime_map.get(content_type, "audio/mp4")

        profile = _get_profile(request.user)
        lang_label = "Turkish" if profile.language == "tr" else "English"

        system_instruction = (
            "You are a food item extraction AI. Listen to the audio recording. "
            "The user is listing food ingredients they have. "
            "Extract every food/ingredient name mentioned. "
            f"Respond ONLY in {lang_label}. "
            "Return ONLY a raw JSON array of objects — no markdown, no code fences. "
            'Each object must have: {"name": "ingredient name", "category": "dairy|meat|vegetable|fruit|grain|beverage|condiment|snack|other"}. '
            "Be specific with ingredient names. "
            "If the audio is unclear or no food items are mentioned, return an empty array: []"
        )

        try:
            client = genai.Client(api_key=settings.GEMINI_API_KEY)
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    {
                        "role": "user",
                        "parts": [
                            {"text": "Extract all food ingredient names from this audio."},
                            {
                                "inline_data": {
                                    "mime_type": mime,
                                    "data": audio_b64,
                                },
                            },
                        ],
                    },
                ],
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                ),
            )
            raw_text = response.text
        except Exception:
            logger.exception("Gemini Audio API call failed")
            return Response(
                {"error": "AI service is temporarily unavailable. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            cleaned = raw_text.strip()
            cleaned = re.sub(r"```(?:json)?", "", cleaned).strip()
            items = json.loads(cleaned)
            if not isinstance(items, list):
                items = []
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.error("Gemini Audio returned unparseable JSON: %s", raw_text[:500])
            return Response(
                {"error": "AI could not understand the audio. Try speaking more clearly."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response({"items": items}, status=status.HTTP_200_OK)


# ======================================================================== #
#  RevenueCat Webhook  –  POST /api/webhooks/revenuecat/
# ======================================================================== #

import hashlib
import hmac


class RevenueCatWebhookView(APIView):
    """
    Receives subscription lifecycle events from RevenueCat.
    Updates the local UserProfile.is_premium flag accordingly.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def _verify_signature(self, request):
        secret = getattr(settings, "REVENUECAT_WEBHOOK_SECRET", "")
        if not secret:
            return True
        sig = request.headers.get("X-RevenueCat-Signature", "")
        body = request.body
        expected = hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(sig, expected)

    def post(self, request):
        if not self._verify_signature(request):
            return Response(
                {"error": "Invalid signature"},
                status=status.HTTP_403_FORBIDDEN,
            )

        event = request.data.get("event", {})
        event_type = event.get("type", "")
        app_user_id = event.get("app_user_id", "")

        if not app_user_id:
            return Response({"ok": True})

        GRANT_EVENTS = {
            "INITIAL_PURCHASE",
            "RENEWAL",
            "PRODUCT_CHANGE",
            "UNCANCELLATION",
        }
        REVOKE_EVENTS = {
            "EXPIRATION",
            "BILLING_ISSUE",
            "CANCELLATION",
        }

        try:
            from django.contrib.auth.models import User

            try:
                user = User.objects.get(pk=int(app_user_id))
            except (User.DoesNotExist, ValueError):
                user = User.objects.filter(username=app_user_id).first()

            if not user:
                logger.warning(
                    "RevenueCat webhook: user not found for app_user_id=%s",
                    app_user_id,
                )
                return Response({"ok": True})

            profile, _ = UserProfile.objects.get_or_create(user=user)

            if event_type in GRANT_EVENTS:
                profile.is_premium = True
                profile.subscription_status = UserProfile.SubscriptionStatus.ACTIVE
                expiration = event.get("expiration_at_ms")
                if expiration:
                    from datetime import datetime
                    profile.premium_until = datetime.fromtimestamp(
                        expiration / 1000, tz=timezone.utc,
                    )
                profile.save(update_fields=[
                    "is_premium", "subscription_status", "premium_until",
                ])
                logger.info(
                    "RevenueCat: GRANT premium for user %s (event=%s)",
                    user.username, event_type,
                )
            elif event_type in REVOKE_EVENTS:
                profile.is_premium = False
                profile.subscription_status = UserProfile.SubscriptionStatus.EXPIRED
                profile.save(update_fields=["is_premium", "subscription_status"])
                logger.info(
                    "RevenueCat: REVOKE premium for user %s (event=%s)",
                    user.username, event_type,
                )
            else:
                logger.info(
                    "RevenueCat: ignored event %s for user %s",
                    event_type, user.username,
                )
        except Exception:
            logger.exception("RevenueCat webhook processing error")

        return Response({"ok": True})
