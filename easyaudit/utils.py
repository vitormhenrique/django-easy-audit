import datetime as dt
import json

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import NOT_PROVIDED, DateTimeField
from django.utils import timezone
from django.utils.encoding import smart_str
from django.db.models.query import QuerySet
from django.db.models.manager import Manager


def get_audit_log_fields(instance_or_class):
    """Get the audit fields for a model instance.

    :param instance: The model instance or model class.
    """
    audit_log_fields = set()
    audit_log_fields_exclude = set()

    # Update set with all fields explicitly defined in
    # the audit_log_fields attribute
    # This may include fields that are related to other models
    # when using the "__" syntax
    # like: audit_log_fields = {"field1", "field2", "related_model__field3"}
    if hasattr(instance_or_class, "audit_log_fields"):
        audit_log_fields.update(instance_or_class.audit_log_fields)
    else:
        # if no audit_log_fields attribute is defined,
        # add all fields to audit_log_fields set
        audit_log_fields.add("*")

    # If "*" is in audit_log_fields, add all model fields
    # to audit_log_fields set
    if "*" in audit_log_fields:
        audit_log_fields.update(
            field.name for field in instance_or_class._meta.fields
        )
        audit_log_fields.remove("*")

    # update exclude set with all fields explicitly defined
    # in the audit_log_fields_exclude attribute
    if hasattr(instance_or_class, "audit_log_fields_exclude"):
        audit_log_fields_exclude.update(
            instance_or_class.audit_log_fields_exclude
        )

    return audit_log_fields - audit_log_fields_exclude


def is_jsonable(x):
    try:
        json.dumps(x)
        return True
    except (TypeError, OverflowError):
        return False


def get_field_value(obj, field):
    """Get the value of a given model instance field.

    :param obj: The model instance.
    :type obj: Model
    :param field: The field you want to find the value of.
    :type field: Any
    :return: The value of the field as a string.
    :rtype: str
    """
    if isinstance(field, DateTimeField):
        # DateTimeFields are timezone-aware, so we need to convert the field
        # to its naive form before we can accurately compare them for changes.
        try:
            value = field.to_python(getattr(obj, field.name, None))
            if value is not None and settings.USE_TZ and not timezone.is_naive(value):
                value = timezone.make_naive(value, timezone=dt.timezone.utc)
        except ObjectDoesNotExist:
            value = field.default if field.default is not NOT_PROVIDED else None
    else:
        try:

            try:
                from pint import Quantity
                value = getattr(obj, field.name, None)
                if isinstance(value, Quantity):                    
                    value = value.to(field.units)
            except ModuleNotFoundError:
                value = smart_str(getattr(obj, field.name, None))

            value = smart_str(value)

        except ObjectDoesNotExist:
            value = field.default if field.default is not NOT_PROVIDED else None

    return value


def model_delta(old_model, new_model):
    """Provide delta/difference between two models.

    :param old: The old state of the model instance.
    :type old: Model
    :param new: The new state of the model instance.
    :type new: Model
    :return: A dictionary with the names of the changed fields as keys and a
             two tuple of the old and new field values
             as value.
    :rtype: dict
    """
    delta = {}
    
    if hasattr(new_model, 'audit_log_fields_exclude'):
        fields = list(field for field in new_model._meta.get_fields() if field.name not in new_model.audit_log_fields_exclude)    

    else:
        fields = new_model._meta.get_fields()

    for field in fields:
        old_value = get_field_value(old_model, field)
        new_value = get_field_value(new_model, field)
        if old_value != new_value:
            delta[field.name] = [smart_str(old_value), smart_str(new_value)]

    # m2m_fields = {relation for relation in new_model.audit_log_fields if '+' in relation}
    # for field in m2m_fields:
    #     old_value = _get_m2m_values(old_model, field)
    #     new_value = _get_m2m_values(new_model, field)
    #     if old_value != new_value:
    #         delta[field] = [old_value, new_value]
    
    if len(delta) == 0:
        delta = None

    return delta


def _get_m2m_values(instance, m2m_field) -> str:
    """
    Serialize the audit fields of the instance
    if field has a __ in it, it will be treated as a nested field
    """

    def _recursive_getattr(obj, field: str):
        
        if not field:
            return obj
        
        fields = field.split("__")

        first_field = fields.pop(0)

        # If there is a * denoting a Many-to-Many relationship
        if '+' in first_field:
            first_field = first_field.replace('+', '')

            obj = getattr(obj, first_field)

            children = []
            children_field_values = []

            if isinstance(obj, Manager):
                children: QuerySet = obj.all().order_by('pk')[:50]
            elif isinstance(obj, QuerySet):
                children = obj

            for obj in children:
                children_field_values.append(_recursive_getattr(obj, "__".join(fields)))

            return children_field_values

        else:
            obj = getattr(obj, first_field)

            return _recursive_getattr(obj, "__".join(fields))

    field_value = _recursive_getattr(instance, m2m_field)

    return str(field_value)


def get_m2m_field_name(model, instance):
    """Find M2M field name on instance.

    Called from m2m_changed signal
    :param model: m2m_changed signal model.
    :type model: Model
    :param instance:m2m_changed signal instance.
    :type new: Model
    :return: ManyToManyField name of instance related to model.
    :rtype: str
    """
    for x in model._meta.related_objects:
        if x.related_model().__class__ == instance.__class__:
            return x.remote_field.name
    return None


def should_propagate_exceptions():
    """Whether Django Easy Audit should propagate signal handler exceptions.

    :rtype: bool
    """
    return getattr(settings, "DJANGO_EASY_AUDIT_PROPAGATE_EXCEPTIONS", False)
