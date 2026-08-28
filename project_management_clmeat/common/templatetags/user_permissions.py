"""Template tags/filters for user permission checking in templates."""
from django import template

register = template.Library()

@register.filter
def has_perm(user, perm):
    """Check if user has a specific permission.
    
    Usage in template:
        {% load user_permissions %}
        {% if user|has_perm:"planning.add_rotationplan" %}
        {% if user|has_perm:"planning.change_rotationplan" %}
        {% if user|has_perm:"inventory.change_package" %}
    """
    if not user or not hasattr(user, 'has_perm'):
        return False
    return user.has_perm(perm)


@register.simple_tag
def has_plan_permission(user):
    """Check if user can create/edit rotation plans."""
    if not user:
        return False
    return (user.is_superuser or 
            user.has_perm('planning.add_rotationplan') or 
            user.has_perm('planning.change_rotationplan'))


@register.simple_tag
def can_manage_inventory(user):
    """Check if user can manage inventory."""
    if not user:
        return False
    return (user.is_superuser or 
            user.has_perm('inventory.change_package') or 
            user.has_perm('inventory.change_product'))
