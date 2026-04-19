"""TarifPlanCategory service implementation."""
import re
from typing import Optional, List
from uuid import UUID
from plugins.subscription.subscription.repositories.tarif_plan_category_repository import TarifPlanCategoryRepository
from plugins.subscription.subscription.repositories.tarif_plan_repository import TarifPlanRepository
from plugins.subscription.subscription.models import TarifPlanCategory


class TarifPlanCategoryService:
    """
    Tariff plan category management service.

    Handles CRUD, plan attachment/detachment, and validation.
    """

    def __init__(
        self,
        category_repo: TarifPlanCategoryRepository,
        tarif_plan_repo: TarifPlanRepository,
    ):
        self._category_repo = category_repo
        self._tarif_plan_repo = tarif_plan_repo

    def get_all(self) -> List[TarifPlanCategory]:
        """Get all categories (flat list)."""
        return self._category_repo.find_all(limit=1000)

    def get_tree(self) -> List[TarifPlanCategory]:
        """Get root categories (with nested children via relationship)."""
        return self._category_repo.find_root_categories()

    def get_by_id(self, category_id: UUID) -> Optional[TarifPlanCategory]:
        """Get category by ID."""
        return self._category_repo.find_by_id(category_id)

    def get_by_slug(self, slug: str) -> Optional[TarifPlanCategory]:
        """Get category by slug."""
        return self._category_repo.find_by_slug(slug)

    def get_categories_for_plan(self, plan_id: UUID) -> List[TarifPlanCategory]:
        """Get all categories containing a plan."""
        return self._category_repo.find_by_plan_id(plan_id)

    def create(
        self,
        name: str,
        slug: Optional[str] = None,
        description: Optional[str] = None,
        parent_id: Optional[UUID] = None,
        is_single: bool = True,
        sort_order: int = 0,
    ) -> TarifPlanCategory:
        """
        Create a new category.

        Raises:
            ValueError: If slug already exists or parent not found.
        """
        if not slug:
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

        existing = self._category_repo.find_by_slug(slug)
        if existing:
            raise ValueError(f"Category with slug '{slug}' already exists")

        if parent_id:
            parent = self._category_repo.find_by_id(parent_id)
            if not parent:
                raise ValueError(f"Parent category {parent_id} not found")

        category = TarifPlanCategory(
            name=name,
            slug=slug,
            description=description,
            parent_id=parent_id,
            is_single=is_single,
            sort_order=sort_order,
        )
        return self._category_repo.save(category)

    def update(
        self,
        category_id: UUID,
        **kwargs,
    ) -> TarifPlanCategory:
        """
        Update category fields.

        Raises:
            ValueError: If category not found, slug conflict, or parent not found.
        """
        category = self._category_repo.find_by_id(category_id)
        if not category:
            raise ValueError("Category not found")

        if "slug" in kwargs and kwargs["slug"] != category.slug:
            existing = self._category_repo.find_by_slug(kwargs["slug"])
            if existing:
                raise ValueError(
                    f"Category with slug '{kwargs['slug']}' already exists"
                )

        if "parent_id" in kwargs and kwargs["parent_id"]:
            if str(kwargs["parent_id"]) == str(category_id):
                raise ValueError("Category cannot be its own parent")
            parent = self._category_repo.find_by_id(kwargs["parent_id"])
            if not parent:
                raise ValueError(f"Parent category {kwargs['parent_id']} not found")

        for key in (
            "name",
            "slug",
            "description",
            "parent_id",
            "is_single",
            "sort_order",
        ):
            if key in kwargs:
                setattr(category, key, kwargs[key])

        return self._category_repo.save(category)

    def delete(self, category_id: UUID) -> bool:
        """
        Delete a category.

        Raises:
            ValueError: If category is root or has children.
        """
        category = self._category_repo.find_by_id(category_id)
        if not category:
            raise ValueError("Category not found")

        if category.slug == "root":
            raise ValueError("Cannot delete root category")

        children = self._category_repo.find_children(category_id)
        if children:
            raise ValueError(
                "Cannot delete category with children. Delete children first."
            )

        return self._category_repo.delete(category_id)

    def attach_plans(
        self, category_id: UUID, plan_ids: List[UUID]
    ) -> TarifPlanCategory:
        """
        Attach plans to a category.

        Raises:
            ValueError: If category or any plan not found.
        """
        category = self._category_repo.find_by_id(category_id)
        if not category:
            raise ValueError("Category not found")

        existing_plan_ids = {str(p.id) for p in category.tarif_plans}

        for plan_id in plan_ids:
            if str(plan_id) in existing_plan_ids:
                continue
            plan = self._tarif_plan_repo.find_by_id(plan_id)
            if not plan:
                raise ValueError(f"Plan {plan_id} not found")
            category.tarif_plans.append(plan)

        return self._category_repo.save(category)

    def detach_plans(
        self, category_id: UUID, plan_ids: List[UUID]
    ) -> TarifPlanCategory:
        """
        Detach plans from a category.

        Raises:
            ValueError: If category not found.
        """
        category = self._category_repo.find_by_id(category_id)
        if not category:
            raise ValueError("Category not found")

        plan_ids_str = {str(pid) for pid in plan_ids}
        category.tarif_plans = [
            p for p in category.tarif_plans if str(p.id) not in plan_ids_str
        ]

        return self._category_repo.save(category)
