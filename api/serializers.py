from rest_framework import serializers

from .models import (
    Ingredient,
    Like,
    MealPlanEntry,
    Recipe,
    RecipeCategory,
    RecipeReport,
    SavedRecipe,
    ShoppingListItem,
    UserProfile,
)


# ---------------------------------------------------------------------------
# UserProfile
# ---------------------------------------------------------------------------

class UserProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = UserProfile
        fields = [
            "id",
            "username",
            "email",
            "is_premium",
            "subscription_status",
            "premium_until",
            "language",
            "diet_type",
            "default_portions",
            "equipment",
            "hometown",
            "onboarding_completed",
            "push_token",
            "expiry_notifications_enabled",
            "email_verified",
            "theme",
        ]
        read_only_fields = [
            "id",
            "is_premium",
            "subscription_status",
            "premium_until",
            "email_verified",
        ]


# ---------------------------------------------------------------------------
# Ingredient (pantry item)
# ---------------------------------------------------------------------------

class IngredientSerializer(serializers.ModelSerializer):
    is_expired_soon = serializers.BooleanField(read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    expiration_date = serializers.DateField(required=False, allow_null=True)

    class Meta:
        model = Ingredient
        fields = [
            "id",
            "name",
            "quantity",
            "category",
            "expiration_date",
            "is_expired_soon",
            "is_expired",
            "created_at",
        ]
        read_only_fields = ["id", "is_expired_soon", "is_expired", "created_at"]

    def validate(self, attrs):
        request = self.context.get("request")
        if request is None:
            return attrs

        user = request.user
        is_premium = getattr(getattr(user, "profile", None), "is_premium", False)

        if not is_premium and not self.instance:
            current_count = Ingredient.objects.filter(
                user=user,
            ).select_for_update().count()
            if current_count >= 5:
                raise serializers.ValidationError(
                    "Free users can save a maximum of 5 pantry ingredients. "
                    "Upgrade to Premium to unlock unlimited storage."
                )
        return attrs


# ---------------------------------------------------------------------------
# Recipe – two variants: full (premium) and restricted (free)
# ---------------------------------------------------------------------------

class RecipeCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = RecipeCategory
        fields = ["id", "slug", "name_tr", "name_en", "icon", "order"]


_RECIPE_BASE_FIELDS = [
    "id",
    "title",
    "prep_time_min",
    "difficulty",
    "category",
    "ingredients_used",
    "missing_ingredients",
    "instructions",
    "image_url",
    "is_ai_generated",
    "is_public",
    "created_by",
    "created_at",
    "updated_at",
    "like_count",
    "is_saved",
]

_RECIPE_MACRO_FIELDS = [
    "total_calories",
    "protein_g",
    "carbs_g",
    "fats_g",
]


class RecipeSerializer(serializers.ModelSerializer):
    """Full recipe serializer — includes macro/calorie data."""

    like_count = serializers.IntegerField(read_only=True, default=0)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    category = RecipeCategorySerializer(read_only=True)
    is_saved = serializers.SerializerMethodField()

    class Meta:
        model = Recipe
        fields = _RECIPE_BASE_FIELDS + _RECIPE_MACRO_FIELDS
        read_only_fields = ["id", "created_at", "updated_at", "like_count"]

    def get_is_saved(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        return SavedRecipe.objects.filter(user=request.user, recipe=obj).exists()


class RecipeFreeSerializer(serializers.ModelSerializer):
    """Restricted serializer — hides macro/calorie fields from free users."""

    like_count = serializers.IntegerField(read_only=True, default=0)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    category = RecipeCategorySerializer(read_only=True)
    is_saved = serializers.SerializerMethodField()

    class Meta:
        model = Recipe
        fields = _RECIPE_BASE_FIELDS
        read_only_fields = ["id", "created_at", "updated_at", "like_count"]

    def get_is_saved(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        return SavedRecipe.objects.filter(user=request.user, recipe=obj).exists()


# ---------------------------------------------------------------------------
# AI generation request payload
# ---------------------------------------------------------------------------

class GenerateRecipeRequestSerializer(serializers.Serializer):
    equipment = serializers.ListField(
        child=serializers.CharField(max_length=60),
        required=False,
        default=list,
        help_text="Kitchen equipment available, e.g. ['oven', 'blender']",
    )
    extra_prompt = serializers.CharField(
        max_length=500,
        required=False,
        default="",
        help_text="Optional free-text hint for the AI",
    )


# ---------------------------------------------------------------------------
# Community share (premium user submits a custom recipe)
# ---------------------------------------------------------------------------

class CommunityShareSerializer(serializers.ModelSerializer):
    """Write-serializer for sharing a recipe to the community feed."""

    class Meta:
        model = Recipe
        fields = [
            "title",
            "prep_time_min",
            "difficulty",
            "total_calories",
            "protein_g",
            "carbs_g",
            "fats_g",
            "instructions",
            "image_url",
        ]

    def validate_image_url(self, value):
        if value and len(value) > 500:
            raise serializers.ValidationError("Image URL must be under 500 characters.")
        return value


# ---------------------------------------------------------------------------
# Like
# ---------------------------------------------------------------------------

class LikeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Like
        fields = ["id", "user", "recipe", "created_at"]
        read_only_fields = ["id", "user", "created_at"]


class ShoppingListItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShoppingListItem
        fields = ["id", "name", "checked", "created_at"]
        read_only_fields = ["id", "created_at"]


class MealPlanEntrySerializer(serializers.ModelSerializer):
    recipe_title = serializers.CharField(source="recipe.title", read_only=True, default="")

    class Meta:
        model = MealPlanEntry
        fields = [
            "id",
            "date",
            "meal_slot",
            "recipe",
            "recipe_title",
            "custom_title",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class RecipeReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecipeReport
        fields = ["id", "recipe", "reason", "status", "created_at"]
        read_only_fields = ["id", "status", "created_at"]
