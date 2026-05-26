from django.core.management.base import BaseCommand

from api.models import RecipeCategory

CATEGORIES = [
    {"slug": "breakfast", "name_tr": "Kahvaltı", "name_en": "Breakfast", "icon": "sunny-outline", "order": 1},
    {"slug": "soup", "name_tr": "Çorbalar", "name_en": "Soups", "icon": "water-outline", "order": 2},
    {"slug": "salad", "name_tr": "Salatalar", "name_en": "Salads", "icon": "leaf-outline", "order": 3},
    {"slug": "main-course", "name_tr": "Ana Yemekler", "name_en": "Main Courses", "icon": "restaurant-outline", "order": 4},
    {"slug": "pasta", "name_tr": "Makarnalar", "name_en": "Pasta", "icon": "pizza-outline", "order": 5},
    {"slug": "dessert", "name_tr": "Tatlılar", "name_en": "Desserts", "icon": "ice-cream-outline", "order": 6},
    {"slug": "snack", "name_tr": "Atıştırmalıklar", "name_en": "Snacks", "icon": "fast-food-outline", "order": 7},
    {"slug": "drink", "name_tr": "İçecekler", "name_en": "Drinks", "icon": "cafe-outline", "order": 8},
    {"slug": "other", "name_tr": "Diğer", "name_en": "Other", "icon": "ellipsis-horizontal-outline", "order": 99},
]


class Command(BaseCommand):
    help = "Seed default recipe categories"

    def handle(self, *args, **options):
        created = 0
        for cat in CATEGORIES:
            _, is_new = RecipeCategory.objects.update_or_create(
                slug=cat["slug"],
                defaults=cat,
            )
            if is_new:
                created += 1
        self.stdout.write(self.style.SUCCESS(f"Categories seeded: {created} new, {len(CATEGORIES)} total"))
