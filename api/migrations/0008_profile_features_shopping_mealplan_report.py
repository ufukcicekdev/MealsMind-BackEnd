from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("api", "0007_alter_recipe_image_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="email_verified",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="expiry_notifications_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="theme",
            field=models.CharField(default="light", max_length=10),
        ),
        migrations.AddField(
            model_name="ingredient",
            name="category",
            field=models.CharField(
                choices=[
                    ("dairy", "Dairy"),
                    ("meat", "Meat"),
                    ("vegetable", "Vegetable"),
                    ("fruit", "Fruit"),
                    ("grain", "Grain"),
                    ("beverage", "Beverage"),
                    ("condiment", "Condiment"),
                    ("snack", "Snack"),
                    ("other", "Other"),
                ],
                default="other",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="ingredient",
            name="quantity",
            field=models.CharField(blank=True, default="", max_length=50),
        ),
        migrations.CreateModel(
            name="ShoppingListItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("checked", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="shopping_items",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["checked", "-created_at"],
            },
        ),
        migrations.CreateModel(
            name="MealPlanEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField()),
                (
                    "meal_slot",
                    models.CharField(
                        choices=[
                            ("breakfast", "Breakfast"),
                            ("lunch", "Lunch"),
                            ("dinner", "Dinner"),
                            ("snack", "Snack"),
                        ],
                        max_length=10,
                    ),
                ),
                ("custom_title", models.CharField(blank=True, default="", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "recipe",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="meal_plan_entries",
                        to="api.recipe",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="meal_plan_entries",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["date", "meal_slot"],
            },
        ),
        migrations.CreateModel(
            name="RecipeReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reason", models.TextField(max_length=500)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("reviewed", "Reviewed"),
                            ("dismissed", "Dismissed"),
                        ],
                        default="pending",
                        max_length=10,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "recipe",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reports",
                        to="api.recipe",
                    ),
                ),
                (
                    "reporter",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recipe_reports",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="shoppinglistitem",
            constraint=models.UniqueConstraint(fields=("user", "name"), name="unique_shopping_item_per_user"),
        ),
        migrations.AddConstraint(
            model_name="mealplanentry",
            constraint=models.UniqueConstraint(
                fields=("user", "date", "meal_slot"), name="unique_meal_slot_per_user_day"
            ),
        ),
        migrations.AddConstraint(
            model_name="recipereport",
            constraint=models.UniqueConstraint(
                fields=("reporter", "recipe"), name="unique_report_per_user_recipe"
            ),
        ),
    ]
