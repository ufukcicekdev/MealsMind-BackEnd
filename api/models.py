from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class UserProfile(models.Model):
    """One-to-one extension of the default Django User model."""

    class SubscriptionStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        TRIAL = "trial", "Trial"

    class Language(models.TextChoices):
        TURKISH = "tr", "Türkçe"
        ENGLISH = "en", "English"

    class DietType(models.TextChoices):
        CLASSIC = "classic", "Classic"
        VEGAN = "vegan", "Vegan"
        VEGETARIAN = "vegetarian", "Vegetarian"
        KETO = "keto", "Keto"
        GLUTEN_FREE = "gluten_free", "Gluten Free"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    is_premium = models.BooleanField(default=False)
    subscription_status = models.CharField(
        max_length=10,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.TRIAL,
    )
    premium_until = models.DateTimeField(null=True, blank=True)
    language = models.CharField(
        max_length=2,
        choices=Language.choices,
        default=Language.TURKISH,
    )
    diet_type = models.CharField(
        max_length=15,
        choices=DietType.choices,
        default=DietType.CLASSIC,
    )
    default_portions = models.PositiveSmallIntegerField(default=4)
    equipment = models.JSONField(default=list, blank=True)
    hometown = models.CharField(max_length=100, blank=True, default="")
    onboarding_completed = models.BooleanField(default=False)
    push_token = models.CharField(max_length=200, blank=True, default="")
    expiry_notifications_enabled = models.BooleanField(default=True)
    email_verified = models.BooleanField(default=False)
    theme = models.CharField(max_length=10, default="light")
    ai_recipe_feedback = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "User Profile"
        verbose_name_plural = "User Profiles"

    def __str__(self):
        return f"{self.user.username} — {self.get_diet_type_display()}"

    @property
    def is_subscription_active(self):
        if self.subscription_status != self.SubscriptionStatus.ACTIVE:
            return False
        if self.premium_until and self.premium_until < timezone.now():
            return False
        return True


class Ingredient(models.Model):
    """A single pantry item owned by a user."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ingredients",
    )
    class Category(models.TextChoices):
        DAIRY = "dairy", "Dairy"
        MEAT = "meat", "Meat"
        VEGETABLE = "vegetable", "Vegetable"
        FRUIT = "fruit", "Fruit"
        GRAIN = "grain", "Grain"
        BEVERAGE = "beverage", "Beverage"
        CONDIMENT = "condiment", "Condiment"
        SNACK = "snack", "Snack"
        OTHER = "other", "Other"

    name = models.CharField(max_length=120)
    quantity = models.CharField(max_length=50, blank=True, default="")
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.OTHER,
    )
    expiration_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ingredient"
        verbose_name_plural = "Ingredients"
        ordering = ["expiration_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "name"],
                name="unique_ingredient_per_user",
            )
        ]

    def __str__(self):
        return f"{self.name} (exp. {self.expiration_date or 'no date'})"

    @property
    def is_expired_soon(self):
        """Return True when the item expires within 3 days but is NOT already expired."""
        if not self.expiration_date:
            return False
        today = timezone.now().date()
        return today <= self.expiration_date <= today + timedelta(days=3)

    @property
    def is_expired(self):
        if not self.expiration_date:
            return False
        return self.expiration_date < timezone.now().date()


class RecipeCategory(models.Model):
    """Predefined recipe categories."""

    slug = models.SlugField(max_length=50, unique=True)
    name_tr = models.CharField(max_length=60)
    name_en = models.CharField(max_length=60)
    icon = models.CharField(max_length=30, blank=True, default="restaurant-outline")
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "Recipe Category"
        verbose_name_plural = "Recipe Categories"
        ordering = ["order"]

    def __str__(self):
        return self.name_en


class Recipe(models.Model):
    """AI-generated or user-submitted recipe."""

    class Difficulty(models.TextChoices):
        EASY = "easy", "Easy"
        MEDIUM = "medium", "Medium"
        HARD = "hard", "Hard"

    title = models.CharField(max_length=255)
    prep_time_min = models.PositiveIntegerField(help_text="Preparation time in minutes")
    difficulty = models.CharField(
        max_length=10,
        choices=Difficulty.choices,
        default=Difficulty.MEDIUM,
    )
    category = models.ForeignKey(
        RecipeCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recipes",
    )
    total_calories = models.PositiveIntegerField(default=0)
    protein_g = models.PositiveIntegerField(default=0)
    carbs_g = models.PositiveIntegerField(default=0)
    fats_g = models.PositiveIntegerField(default=0)
    ingredients_used = models.JSONField(default=list, blank=True)
    missing_ingredients = models.JSONField(default=list, blank=True)
    instructions = models.JSONField(
        default=list,
        help_text="Ordered list of instruction steps",
    )
    image_url = models.CharField(max_length=500, blank=True, default="")
    is_ai_generated = models.BooleanField(default=False)
    is_public = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recipes",
    )
    servings = models.PositiveSmallIntegerField(default=4)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Recipe"
        verbose_name_plural = "Recipes"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} ({self.get_difficulty_display()}, {self.prep_time_min} min)"


class AIGenerationLog(models.Model):
    """Tracks AI generate/quick calls for daily free-tier limits."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ai_generation_logs",
    )
    mode = models.CharField(max_length=20, default="standard")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def macro_summary(self):
        return {
            "calories": self.total_calories,
            "protein": self.protein_g,
            "carbs": self.carbs_g,
            "fats": self.fats_g,
        }


class SavedRecipe(models.Model):
    """Bookmarked recipe per user (personal collection)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_recipes",
    )
    recipe = models.ForeignKey(
        Recipe,
        on_delete=models.CASCADE,
        related_name="saved_by",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Saved Recipe"
        verbose_name_plural = "Saved Recipes"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "recipe"],
                name="unique_saved_recipe_per_user",
            )
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} ★ {self.recipe.title}"


class Like(models.Model):
    """Tracks a user 'liking' a recipe — drives community interactions."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="likes",
    )
    recipe = models.ForeignKey(
        Recipe,
        on_delete=models.CASCADE,
        related_name="likes",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Like"
        verbose_name_plural = "Likes"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "recipe"],
                name="unique_like_per_user_recipe",
            )
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} ❤ {self.recipe.title}"


class ShoppingListItem(models.Model):
    """User shopping list (often from recipe missing ingredients)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shopping_items",
    )
    name = models.CharField(max_length=120)
    checked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["checked", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "name"],
                name="unique_shopping_item_per_user",
            )
        ]

    def __str__(self):
        return self.name


class MealPlanEntry(models.Model):
    """One meal slot on a given day."""

    class MealSlot(models.TextChoices):
        BREAKFAST = "breakfast", "Breakfast"
        LUNCH = "lunch", "Lunch"
        DINNER = "dinner", "Dinner"
        SNACK = "snack", "Snack"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="meal_plan_entries",
    )
    date = models.DateField()
    meal_slot = models.CharField(max_length=10, choices=MealSlot.choices)
    recipe = models.ForeignKey(
        Recipe,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="meal_plan_entries",
    )
    custom_title = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "meal_slot"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "date", "meal_slot"],
                name="unique_meal_slot_per_user_day",
            )
        ]

    def __str__(self):
        title = self.recipe.title if self.recipe else self.custom_title
        return f"{self.date} {self.meal_slot}: {title}"


class RecipeReport(models.Model):
    """Community content moderation report."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        REVIEWED = "reviewed", "Reviewed"
        DISMISSED = "dismissed", "Dismissed"

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recipe_reports",
    )
    recipe = models.ForeignKey(
        Recipe,
        on_delete=models.CASCADE,
        related_name="reports",
    )
    reason = models.TextField(max_length=500)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["reporter", "recipe"],
                name="unique_report_per_user_recipe",
            )
        ]

    def __str__(self):
        return f"Report on {self.recipe_id} by {self.reporter_id}"
