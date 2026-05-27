from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views
from .auth_views import (
    AppleAuthView,
    ChangePasswordView,
    DeleteAccountView,
    ForgotPasswordView,
    GoogleAuthView,
    LoginView,
    RefreshTokenView,
    RegisterView,
    ResendVerificationView,
    ResetPasswordView,
    VerifyEmailView,
)

router = DefaultRouter()
router.register(r"ingredients", views.IngredientViewSet, basename="ingredient")

app_name = "api"

urlpatterns = [
    # Auth
    path("auth/register/", RegisterView.as_view(), name="auth-register"),
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/refresh/", RefreshTokenView.as_view(), name="auth-refresh"),
    path("auth/forgot-password/", ForgotPasswordView.as_view(), name="auth-forgot-password"),
    path("auth/reset-password/", ResetPasswordView.as_view(), name="auth-reset-password"),
    path("auth/google/", GoogleAuthView.as_view(), name="auth-google"),
    path("auth/apple/", AppleAuthView.as_view(), name="auth-apple"),
    path("auth/change-password/", ChangePasswordView.as_view(), name="auth-change-password"),
    path("auth/delete-account/", DeleteAccountView.as_view(), name="auth-delete-account"),
    path("auth/verify-email/", VerifyEmailView.as_view(), name="auth-verify-email"),
    path(
        "auth/resend-verification/",
        ResendVerificationView.as_view(),
        name="auth-resend-verification",
    ),
    # Profile
    path("profile/", views.UserProfileView.as_view(), name="user-profile"),
    # Categories
    path("categories/", views.RecipeCategoryListView.as_view(), name="category-list"),
    # Recipes
    path("recipes/generate/", views.GenerateRecipeView.as_view(), name="generate-recipe"),
    path("recipes/create-from-photo/", views.CreateRecipeFromPhotoView.as_view(), name="create-from-photo"),
    path("recipes/save-draft/", views.SaveDraftRecipeView.as_view(), name="save-draft"),
    path("recipes/saved/", views.MyRecipesView.as_view(), name="my-recipes"),
    path("recipes/<int:recipe_id>/like/", views.LikeToggleView.as_view(), name="like-toggle"),
    path("recipes/<int:recipe_id>/save/", views.SaveRecipeToggleView.as_view(), name="save-toggle"),
    # Pantry AI (scan & voice)
    path("pantry/scan/", views.PantryScanView.as_view(), name="pantry-scan"),
    path("pantry/voice/", views.PantryVoiceView.as_view(), name="pantry-voice"),
    # Community
    path("community/recipes/", views.CommunityRecipeListView.as_view(), name="community-list"),
    path("community/recipes/share/", views.CommunityShareView.as_view(), name="community-share"),
    path("community/categories/", views.CommunityCategoryStatsView.as_view(), name="community-categories"),
    path(
        "community/recipes/<int:recipe_id>/report/",
        views.RecipeReportView.as_view(),
        name="community-report",
    ),
    # Shopping list
    path("shopping/", views.ShoppingListViewSet.as_view({"get": "list", "post": "create"}), name="shopping-list"),
    path(
        "shopping/<int:pk>/",
        views.ShoppingListViewSet.as_view(
            {"get": "retrieve", "patch": "partial_update", "delete": "destroy"},
        ),
        name="shopping-detail",
    ),
    path("shopping/bulk/", views.ShoppingListBulkAddView.as_view(), name="shopping-bulk"),
    # Meal plan
    path("meal-plan/", views.MealPlanListView.as_view(), name="meal-plan-list"),
    path("meal-plan/entries/", views.MealPlanEntryView.as_view(), name="meal-plan-create"),
    path(
        "meal-plan/entries/<int:entry_id>/",
        views.MealPlanEntryView.as_view(),
        name="meal-plan-delete",
    ),
    # Webhooks
    path("webhooks/revenuecat/", views.RevenueCatWebhookView.as_view(), name="revenuecat-webhook"),
    # CRUD
    path("", include(router.urls)),
]
