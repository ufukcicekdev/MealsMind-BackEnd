import hashlib
import logging
import random
import string

import jwt
import requests as http_requests
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .email_service import send_app_email
from .models import UserProfile

logger = logging.getLogger("api")


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, min_length=3)
    email = serializers.EmailField()
    password = serializers.CharField(min_length=6, write_only=True)

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Bu kullanıcı adı zaten alınmış.")
        return value.lower()

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Bu e-posta adresi zaten kayıtlı.")
        return value.lower()


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)


def _build_token_response(user: User) -> dict:
    refresh = RefreshToken.for_user(user)
    profile, _ = UserProfile.objects.get_or_create(user=user)

    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "is_premium": profile.is_premium,
            "language": profile.language,
            "diet_type": profile.diet_type,
            "default_portions": profile.default_portions,
            "equipment": profile.equipment,
            "hometown": profile.hometown,
            "onboarding_completed": profile.onboarding_completed,
            "email_verified": profile.email_verified,
            "expiry_notifications_enabled": profile.expiry_notifications_enabled,
            "theme": profile.theme,
        },
    }


def _send_verification_email(user: User) -> bool:
    if not user.email:
        return False
    if not getattr(settings, "SMTP2GO_API_KEY", ""):
        logger.error("SMTP2GO_API_KEY not configured — cannot send verification email")
        return False

    code = _generate_code()
    cache_key = f"email_verify:{user.email.lower()}"
    cache.set(cache_key, {"code": code, "user_id": user.pk}, timeout=86400)
    try:
        html = (
            '<div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;">'
            '<h2 style="color:#059669;">MealsMind</h2>'
            "<p>Your email verification code is:</p>"
            f'<div style="font-size:32px;font-weight:800;letter-spacing:8px;text-align:center;'
            f'padding:20px;background:#f3f4f6;border-radius:12px;">{code}</div>'
            "</div>"
        )
        return send_app_email(
            to=[user.email],
            subject="MealsMind — Verify your email",
            text_body=f"Your email verification code is: {code}\n\nThis code expires in 24 hours.",
            html_body=html,
        )
    except Exception:
        logger.exception("Failed to send verification email to %s", user.email)
        return False


def _mark_social_email_verified(user: User) -> None:
    """Google/Apple already verify the user's email — skip manual verification."""
    profile, _ = UserProfile.objects.get_or_create(user=user)
    if not profile.email_verified:
        profile.email_verified = True
        profile.save(update_fields=["email_verified"])


class RegisterView(APIView):
    """POST /api/auth/register/ — create account and return JWT tokens."""

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        ser = RegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        user = User.objects.create_user(
            username=ser.validated_data["username"],
            email=ser.validated_data["email"],
            password=ser.validated_data["password"],
        )
        _send_verification_email(user)

        return Response(
            _build_token_response(user),
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    """POST /api/auth/login/ — authenticate and return JWT tokens."""

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        ser = LoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        username = ser.validated_data["username"].lower()
        password = ser.validated_data["password"]

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            return Response(
                {"detail": "Geçersiz kullanıcı adı veya şifre."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.check_password(password):
            return Response(
                {"detail": "Geçersiz kullanıcı adı veya şifre."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        return Response(_build_token_response(user), status=status.HTTP_200_OK)


class RefreshTokenView(APIView):
    """POST /api/auth/refresh/ — get a new access token."""

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        token = request.data.get("refresh")
        if not token:
            return Response(
                {"detail": "Refresh token gerekli."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            refresh = RefreshToken(token)
            return Response({
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            })
        except Exception:
            return Response(
                {"detail": "Geçersiz veya süresi dolmuş token."},
                status=status.HTTP_401_UNAUTHORIZED,
            )


class ChangePasswordView(APIView):
    """POST /api/auth/change-password/ — change the current user's password."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        current = request.data.get("current_password", "")
        new_pw = request.data.get("new_password", "")

        if not current or not new_pw:
            return Response(
                {"detail": "Both current_password and new_password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if len(new_pw) < 6:
            return Response(
                {"detail": "New password must be at least 6 characters."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not request.user.check_password(current):
            return Response(
                {"detail": "Current password is incorrect."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        request.user.set_password(new_pw)
        request.user.save()

        if request.user.email:
            send_app_email(
                to=[request.user.email],
                subject="MealsMind — Password Changed",
                text_body=(
                    "Your MealsMind account password was changed. "
                    "If you did not do this, please reset your password immediately."
                ),
                html_body=(
                    '<div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;">'
                    '<h2 style="color:#059669;">MealsMind</h2>'
                    "<p>Your account password was changed successfully.</p>"
                    '<p style="color:#6b7280;">If you did not make this change, please reset your password immediately.</p>'
                    "</div>"
                ),
            )

        return Response({"detail": "Password changed successfully."})


class DeleteAccountView(APIView):
    """POST /api/auth/delete-account/ — permanently delete the user account."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        password = request.data.get("password", "")

        if not password:
            return Response(
                {"detail": "Password is required to confirm account deletion."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not request.user.check_password(password):
            return Response(
                {"detail": "Incorrect password."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        request.user.delete()
        return Response(
            {"detail": "Account deleted successfully."},
            status=status.HTTP_200_OK,
        )


# ======================================================================== #
#  Forgot Password  –  Request reset code via email
# ======================================================================== #

def _generate_code(length=6):
    return "".join(random.choices(string.digits, k=length))


class ForgotPasswordView(APIView):
    """
    POST /api/auth/forgot-password/
    Body: { "email": "user@example.com" }
    Sends a 6-digit reset code to the user's email.
    """

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = request.data.get("email", "").strip().lower()
        if not email:
            return Response(
                {"detail": "Email is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Always return success to prevent email enumeration
        success_msg = {"detail": "If an account with that email exists, a reset code has been sent."}

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            return Response(success_msg)

        code = _generate_code()
        cache_key = f"pwd_reset:{email}"
        cache.set(cache_key, {"code": code, "user_id": user.pk}, timeout=600)

        html = (
            f'<div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;">'
            f'<h2 style="color:#059669;">MealsMind</h2>'
            f'<p>Your password reset code is:</p>'
            f'<div style="font-size:32px;font-weight:800;letter-spacing:8px;color:#111827;'
            f'background:#f3f4f6;border-radius:12px;padding:20px;text-align:center;margin:20px 0;">'
            f'{code}</div>'
            f'<p style="color:#6b7280;">This code expires in 10 minutes.</p>'
            f'<p style="color:#9ca3af;font-size:13px;">If you did not request this, please ignore this email.</p>'
            f'</div>'
        )
        if not send_app_email(
            to=[email],
            subject="MealsMind — Password Reset Code",
            text_body=(
                f"Your password reset code is: {code}\n\n"
                "This code expires in 10 minutes.\n\n"
                "If you did not request this, please ignore this email."
            ),
            html_body=html,
        ):
            return Response(
                {"detail": "Could not send email. Please try again later."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(success_msg)


class ResetPasswordView(APIView):
    """
    POST /api/auth/reset-password/
    Body: { "email": "...", "code": "123456", "new_password": "..." }
    Verifies the code and sets the new password.
    """

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = request.data.get("email", "").strip().lower()
        code = request.data.get("code", "").strip()
        new_password = request.data.get("new_password", "")

        if not email or not code or not new_password:
            return Response(
                {"detail": "email, code, and new_password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if len(new_password) < 6:
            return Response(
                {"detail": "Password must be at least 6 characters."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cache_key = f"pwd_reset:{email}"
        cached = cache.get(cache_key)

        if not cached or cached.get("code") != code:
            return Response(
                {"detail": "Invalid or expired code."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(pk=cached["user_id"])
        except User.DoesNotExist:
            return Response(
                {"detail": "User not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        user.set_password(new_password)
        user.save()
        cache.delete(cache_key)

        return Response({"detail": "Password has been reset successfully."})


# ======================================================================== #
#  Email verification
# ======================================================================== #

class VerifyEmailView(APIView):
    """POST /api/auth/verify-email/ — body: { "code": "123456" }"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        code = request.data.get("code", "").strip()
        if not code:
            return Response(
                {"detail": "code is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        email = request.user.email.lower()
        cached = cache.get(f"email_verify:{email}")
        if not cached or cached.get("code") != code:
            return Response(
                {"detail": "Invalid or expired code."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        profile = UserProfile.objects.get(user=request.user)
        profile.email_verified = True
        profile.save(update_fields=["email_verified"])
        cache.delete(f"email_verify:{email}")
        return Response({"detail": "Email verified successfully."})


class ResendVerificationView(APIView):
    """POST /api/auth/resend-verification/"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        profile = UserProfile.objects.get(user=request.user)
        if profile.email_verified:
            return Response({"detail": "Email is already verified."})
        if not request.user.email:
            return Response(
                {"detail": "No email address on this account."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not _send_verification_email(request.user):
            return Response(
                {"detail": "Could not send verification email. Please try again later."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"detail": "Verification code sent."})


# ======================================================================== #
#  Social Auth  –  Google Sign-In
# ======================================================================== #

class GoogleAuthView(APIView):
    """
    POST /api/auth/google/
    Body: { "id_token": "<Google ID token>" }
    Verifies the token with Google, creates or gets user, returns JWT.
    """

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        id_token = request.data.get("id_token", "")
        if not id_token:
            return Response(
                {"detail": "id_token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            resp = http_requests.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": id_token},
                timeout=10,
            )
            if resp.status_code != 200:
                return Response(
                    {"detail": "Invalid Google token."},
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            payload = resp.json()
            google_client_id = getattr(settings, "GOOGLE_CLIENT_ID", "")
            if google_client_id and payload.get("aud") != google_client_id:
                return Response(
                    {"detail": "Token audience mismatch."},
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            google_email = payload.get("email", "")
            google_name = payload.get("name", "")
            google_sub = payload.get("sub", "")

            if not google_email:
                return Response(
                    {"detail": "Email not provided by Google."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user = User.objects.filter(email__iexact=google_email).first()

            if not user:
                base_username = google_email.split("@")[0].lower()[:30]
                username = base_username
                counter = 1
                while User.objects.filter(username=username).exists():
                    username = f"{base_username}{counter}"
                    counter += 1

                user = User.objects.create_user(
                    username=username,
                    email=google_email.lower(),
                    password=None,
                )
                user.first_name = google_name[:30] if google_name else ""
                user.save(update_fields=["first_name"])

            _mark_social_email_verified(user)

            return Response(
                _build_token_response(user),
                status=status.HTTP_200_OK,
            )

        except http_requests.RequestException:
            logger.exception("Google token verification network error")
            return Response(
                {"detail": "Could not verify Google token. Try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


# ======================================================================== #
#  Social Auth  –  Apple Sign-In
# ======================================================================== #

_APPLE_KEYS_CACHE: dict = {}


def _get_apple_public_keys():
    """Fetch Apple's public keys (cached in-memory)."""
    if _APPLE_KEYS_CACHE.get("keys"):
        return _APPLE_KEYS_CACHE["keys"]

    resp = http_requests.get(
        "https://appleid.apple.com/auth/keys", timeout=10,
    )
    resp.raise_for_status()
    keys = resp.json().get("keys", [])
    _APPLE_KEYS_CACHE["keys"] = keys
    return keys


class AppleAuthView(APIView):
    """
    POST /api/auth/apple/
    Body: { "id_token": "<Apple identity token>", "full_name": "..." }
    Verifies the token against Apple's public keys, creates or gets user, returns JWT.
    """

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        id_token = request.data.get("id_token", "")
        full_name = request.data.get("full_name", "")

        if not id_token:
            return Response(
                {"detail": "id_token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            header = jwt.get_unverified_header(id_token)
            apple_keys = _get_apple_public_keys()

            matching_key = None
            for key in apple_keys:
                if key.get("kid") == header.get("kid"):
                    matching_key = key
                    break

            if not matching_key:
                _APPLE_KEYS_CACHE.clear()
                apple_keys = _get_apple_public_keys()
                for key in apple_keys:
                    if key.get("kid") == header.get("kid"):
                        matching_key = key
                        break

            if not matching_key:
                return Response(
                    {"detail": "Invalid Apple token (key not found)."},
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            public_key = jwt.algorithms.RSAAlgorithm.from_jwk(matching_key)

            apple_bundle_id = getattr(settings, "APPLE_BUNDLE_ID", "com.mealsmind.app")

            payload = jwt.decode(
                id_token,
                key=public_key,
                algorithms=["RS256"],
                audience=apple_bundle_id,
                issuer="https://appleid.apple.com",
            )

            apple_sub = payload.get("sub", "")
            apple_email = payload.get("email", "")

            if not apple_sub:
                return Response(
                    {"detail": "Apple token missing subject."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user = None
            if apple_email:
                user = User.objects.filter(email__iexact=apple_email).first()

            if not user:
                apple_hash = hashlib.md5(apple_sub.encode()).hexdigest()[:12]
                base_username = f"apple_{apple_hash}"
                username = base_username
                counter = 1
                while User.objects.filter(username=username).exists():
                    username = f"{base_username}{counter}"
                    counter += 1

                user = User.objects.create_user(
                    username=username,
                    email=(apple_email or f"{apple_sub}@privaterelay.appleid.com").lower(),
                    password=None,
                )
                if full_name:
                    user.first_name = full_name[:30]
                    user.save(update_fields=["first_name"])

            _mark_social_email_verified(user)

            return Response(
                _build_token_response(user),
                status=status.HTTP_200_OK,
            )

        except jwt.ExpiredSignatureError:
            return Response(
                {"detail": "Apple token has expired."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        except jwt.InvalidTokenError:
            return Response(
                {"detail": "Invalid Apple token."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        except http_requests.RequestException:
            logger.exception("Apple key fetch network error")
            return Response(
                {"detail": "Could not verify Apple token. Try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
