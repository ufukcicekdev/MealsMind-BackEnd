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
    ResetPasswordView,
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
    # Webhooks
    path("webhooks/revenuecat/", views.RevenueCatWebhookView.as_view(), name="revenuecat-webhook"),
    # CRUD
    path("", include(router.urls)),
]
