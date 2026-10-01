"""circe-be cohort definition model and emulation of Jackson (2.11, OHDSI Utils config) deserialization.

Config: FAIL_ON_UNKNOWN_PROPERTIES off, case-sensitive names, otherwise Jackson defaults
(float->int truncation, string->number coercion, null->primitive default, enums by name or ordinal).
"""
import json

from ._jutil import CirceError

INT_MIN, INT_MAX = -2**31, 2**31 - 1
LONG_MIN, LONG_MAX = -2**63, 2**63 - 1


def _bad(kind, v):
    raise CirceError(f'MismatchedInputException: cannot deserialize {kind} from {type(v).__name__}')


def _parse_int_text(text, lo, hi, kind):
    t = text.strip()
    if t == '' or t == 'null':
        return None
    try:
        if not t.lstrip('+-').isdigit():
            raise ValueError
        v = int(t)
    except ValueError:
        raise CirceError(f'InvalidFormatException: not a valid {kind} value: "{text}"')
    if not lo <= v <= hi:
        raise CirceError(f'InvalidFormatException: out of range of {kind}')
    return v


def _int_like(v, lo, hi, kind, primitive):
    if v is None:
        return 0 if primitive else None
    if isinstance(v, bool):
        _bad(kind, v)
    if isinstance(v, int):
        if not lo <= v <= hi:
            raise CirceError(f'InputCoercionException: Numeric value ({v}) out of range of {kind}')
        return v
    if isinstance(v, float):
        if v != v or not lo <= v <= hi + 0.999999:     # Jackson: out-of-range float is an error
            if v != v:
                return 0
            raise CirceError(f'InputCoercionException: Numeric value ({v}) out of range of {kind}')
        return int(v)                                   # truncate toward zero
    if isinstance(v, str):
        r = _parse_int_text(v, lo, hi, kind)
        return (0 if primitive else None) if r is None else r
    _bad(kind, v)


def to_int(v):
    return _int_like(v, INT_MIN, INT_MAX, 'int', True)


def to_integer(v):
    return _int_like(v, INT_MIN, INT_MAX, 'Integer', False)


def to_long(v):
    return _int_like(v, LONG_MIN, LONG_MAX, 'Long', False)


def _bool_like(v, primitive):
    if v is None:
        return False if primitive else None
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v != 0
    if isinstance(v, str):
        t = v.strip()
        if t in ('true', 'True', 'TRUE'):
            return True
        if t in ('false', 'False', 'FALSE'):
            return False
        if t == '' or t == 'null':
            return False if primitive else None
        raise CirceError(f'InvalidFormatException: not a valid Boolean value: "{v}"')
    _bad('boolean', v)


def to_bool(v):
    return _bool_like(v, True)


def to_boolean(v):
    return _bool_like(v, False)


def to_str(v):
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (int, float)):
        return json.dumps(v)
    _bad('String', v)


class JNumber:
    """java.lang.Number (Integer/Long/BigInteger/Double) value."""
    __slots__ = ('v', 'kind')

    def __init__(self, v, kind):
        self.v, self.kind = v, kind

    def int_value(self):
        if self.kind == 'double':
            d = self.v
            if d != d:
                return 0
            if d >= INT_MAX:
                return INT_MAX
            if d <= INT_MIN:
                return INT_MIN
            return int(d)
        n = self.v & 0xFFFFFFFF
        return n - 0x100000000 if n & 0x80000000 else n

    def double_value(self):
        return float(self.v)


def to_number(v):
    if v is None:
        return None
    if isinstance(v, bool):
        _bad('Number', v)
    if isinstance(v, int):
        return JNumber(v, 'int')
    if isinstance(v, float):
        return JNumber(v, 'double')
    if isinstance(v, str):
        t = v.strip()
        if t == '' or t == 'null':
            return None
        if t.lstrip('+-').isdigit():
            return JNumber(int(t), 'int')
        try:
            return JNumber(float(t), 'double')
        except ValueError:
            raise CirceError(f'InvalidFormatException: not a valid number: "{v}"')
    _bad('Number', v)


def enum_of(names):
    """By name or ordinal."""
    def conv(v):
        if v is None:
            return None
        if isinstance(v, bool):
            _bad('enum', v)
        if isinstance(v, int):
            if 0 <= v < len(names):
                return names[v]
            raise CirceError(f'InvalidFormatException: index {v} out of enum range')
        if isinstance(v, str):
            if v in names:
                return v
            raise CirceError(f'InvalidFormatException: "{v}" not one of {names}')
        _bad('enum', v)
    return conv


def array_of(conv):
    def f(v):
        if v is None:
            return None
        if not isinstance(v, list):
            _bad('array', v)
        return [conv(x) for x in v]
    return f


class Bean:
    """Field definitions: _fields = [(json name, attribute, converter, default factory)]."""
    _fields = ()

    def __init__(self):
        for _, attr, _, default in self._all_fields():
            setattr(self, attr, default() if callable(default) else default)

    @classmethod
    def _all_fields(cls):
        out = []
        for k in reversed(cls.__mro__):
            out.extend(k.__dict__.get('_fields', ()))
        return out

    @classmethod
    def parse(cls, v):
        if v is None:
            return None
        if not isinstance(v, dict):
            _bad(cls.__name__, v)
        obj = cls()
        table = {j: (a, c) for j, a, c, _ in cls._all_fields()}
        for k, x in v.items():
            if k in table:
                attr, conv = table[k]
                setattr(obj, attr, conv(x))
        return obj


def bean(cls):
    return cls.parse


# ---- vocabulary --------------------------------------------------------------

class Concept(Bean):
    _fields = [('CONCEPT_ID', 'concept_id', to_long, None), ('CONCEPT_NAME', 'concept_name', to_str, None),
               ('STANDARD_CONCEPT', 'standard_concept', to_str, None), ('INVALID_REASON', 'invalid_reason', to_str, None),
               ('CONCEPT_CODE', 'concept_code', to_str, None), ('DOMAIN_ID', 'domain_id', to_str, None),
               ('VOCABULARY_ID', 'vocabulary_id', to_str, None), ('CONCEPT_CLASS_ID', 'concept_class_id', to_str, None)]


class ConceptSetItem(Bean):
    _fields = [('concept', 'concept', Concept.parse, None), ('isExcluded', 'is_excluded', to_bool, False),
               ('includeDescendants', 'include_descendants', to_bool, False),
               ('includeMapped', 'include_mapped', to_bool, False)]


class ConceptSetExpression(Bean):
    _fields = [('items', 'items', array_of(ConceptSetItem.parse), None)]


# ---- cohort definition -------------------------------------------------------

class ConceptSet(Bean):
    _fields = [('id', 'id', to_int, 0), ('name', 'name', to_str, None),
               ('expression', 'expression', ConceptSetExpression.parse, None)]


class ConceptSetSelection(Bean):
    _fields = [('CodesetId', 'codeset_id', to_integer, None), ('IsExclusion', 'is_exclusion', to_bool, False)]


class DateRange(Bean):
    _fields = [('Value', 'value', to_str, None), ('Op', 'op', to_str, None), ('Extent', 'extent', to_str, None)]


class NumericRange(Bean):
    _fields = [('Value', 'value', to_number, None), ('Op', 'op', to_str, None), ('Extent', 'extent', to_number, None)]


class TextFilter(Bean):
    _fields = [('Text', 'text', to_str, None), ('Op', 'op', to_str, None)]


class Period(Bean):
    _fields = [('StartDate', 'start_date', to_str, None), ('EndDate', 'end_date', to_str, None)]


DATE_TYPES = ['START_DATE', 'END_DATE']


class DateAdjustment(Bean):
    _fields = [('StartWith', 'start_with', enum_of(DATE_TYPES), 'START_DATE'), ('StartOffset', 'start_offset', to_int, 0),
               ('EndWith', 'end_with', enum_of(DATE_TYPES), 'END_DATE'), ('EndOffset', 'end_offset', to_int, 0)]


CRITERIA_COLUMNS = ['DAYS_SUPPLY', 'DOMAIN_CONCEPT', 'DOMAIN_SOURCE_CONCEPT', 'DURATION', 'END_DATE', 'ERA_OCCURRENCES',
                    'GAP_DAYS', 'QUANTITY', 'RANGE_HIGH', 'RANGE_LOW', 'REFILLS', 'START_DATE', 'UNIT',
                    'VALUE_AS_NUMBER', 'VISIT_ID', 'VISIT_DETAIL_ID']
COLUMN_NAME = {'DAYS_SUPPLY': 'days_supply', 'DOMAIN_CONCEPT': 'domain_concept_id',
               'DOMAIN_SOURCE_CONCEPT': 'domain_source_concept_id', 'DURATION': 'duration', 'END_DATE': 'end_date',
               'ERA_OCCURRENCES': 'occurrence_count', 'GAP_DAYS': 'gap_days', 'QUANTITY': 'quantity',
               'RANGE_HIGH': 'range_high', 'RANGE_LOW': 'range_low', 'REFILLS': 'refills', 'START_DATE': 'start_date',
               'UNIT': 'unit_concept_id', 'VALUE_AS_NUMBER': 'value_as_number', 'VISIT_ID': 'visit_occurrence_id',
               'VISIT_DETAIL_ID': 'visit_detail_id'}


class Occurrence(Bean):
    EXACTLY, AT_MOST, AT_LEAST = 0, 1, 2
    _fields = [('Type', 'type', to_int, 0), ('Count', 'count', to_int, 0), ('IsDistinct', 'is_distinct', to_bool, False),
               ('CountColumn', 'count_column', enum_of(CRITERIA_COLUMNS), None)]


class Endpoint(Bean):
    _fields = [('Days', 'days', to_integer, None), ('Coeff', 'coeff', to_int, 0)]


class Window(Bean):
    _fields = [('Start', 'start', Endpoint.parse, None), ('End', 'end', Endpoint.parse, None),
               ('UseIndexEnd', 'use_index_end', to_boolean, None), ('UseEventEnd', 'use_event_end', to_boolean, None)]


def _criteria(v):
    return parse_criteria(v)


def _group(v):
    return CriteriaGroup.parse(v)


class WindowedCriteria(Bean):
    _fields = [('Criteria', 'criteria', _criteria, None), ('StartWindow', 'start_window', Window.parse, None),
               ('EndWindow', 'end_window', Window.parse, None), ('RestrictVisit', 'restrict_visit', to_bool, False),
               ('IgnoreObservationPeriod', 'ignore_observation_period', to_bool, False)]


class CorelatedCriteria(WindowedCriteria):
    _fields = [('Occurrence', 'occurrence', Occurrence.parse, None)]


class DemographicCriteria(Bean):
    _fields = [('Age', 'age', NumericRange.parse, None), ('Gender', 'gender', array_of(Concept.parse), None),
               ('GenderCS', 'gender_cs', ConceptSetSelection.parse, None),
               ('Race', 'race', array_of(Concept.parse), None), ('RaceCS', 'race_cs', ConceptSetSelection.parse, None),
               ('Ethnicity', 'ethnicity', array_of(Concept.parse), None),
               ('EthnicityCS', 'ethnicity_cs', ConceptSetSelection.parse, None),
               ('OccurrenceStartDate', 'occurrence_start_date', DateRange.parse, None),
               ('OccurrenceEndDate', 'occurrence_end_date', DateRange.parse, None)]


class CriteriaGroup(Bean):
    _fields = [('Type', 'type', to_str, None), ('Count', 'count', to_integer, None),
               ('CriteriaList', 'criteria_list', array_of(CorelatedCriteria.parse), list),
               ('DemographicCriteriaList', 'demographic_criteria_list', array_of(DemographicCriteria.parse), list),
               ('Groups', 'groups', array_of(_group), list)]

    def is_empty(self):
        return not (len(_nn(self.criteria_list)) > 0 or len(_nn(self.demographic_criteria_list)) > 0
                    or len(_nn(self.groups)) > 0)


def _nn(x):
    if x is None:
        raise CirceError('NullPointerException')
    return x


_concepts = array_of(Concept.parse)
_cs = ConceptSetSelection.parse
_dr = DateRange.parse
_nr = NumericRange.parse
_tf = TextFilter.parse


class Criteria(Bean):
    TYPE = None
    _fields = [('CorrelatedCriteria', 'correlated_criteria', _group, None),
               ('DateAdjustment', 'date_adjustment', DateAdjustment.parse, None)]


def _common(*names):
    """Fields repeated across many criteria."""
    table = {
        'CodesetId': ('codeset_id', to_integer, None), 'First': ('first', to_boolean, None),
        'OccurrenceStartDate': ('occurrence_start_date', _dr, None),
        'OccurrenceEndDate': ('occurrence_end_date', _dr, None),
        'Age': ('age', _nr, None), 'Gender': ('gender', _concepts, None), 'GenderCS': ('gender_cs', _cs, None),
        'ProviderSpecialty': ('provider_specialty', _concepts, None),
        'ProviderSpecialtyCS': ('provider_specialty_cs', _cs, None),
        'VisitType': ('visit_type', _concepts, None), 'VisitTypeCS': ('visit_type_cs', _cs, None),
        'AgeAtStart': ('age_at_start', _nr, None), 'AgeAtEnd': ('age_at_end', _nr, None),
        'EraStartDate': ('era_start_date', _dr, None), 'EraEndDate': ('era_end_date', _dr, None),
        'EraLength': ('era_length', _nr, None), 'OccurrenceCount': ('occurrence_count', _nr, None),
        'Unit': ('unit', _concepts, None), 'UnitCS': ('unit_cs', _cs, None), 'Quantity': ('quantity', _nr, None),
        'ValueAsNumber': ('value_as_number', _nr, None), 'ValueAsConcept': ('value_as_concept', _concepts, None),
        'ValueAsConceptCS': ('value_as_concept_cs', _cs, None), 'StopReason': ('stop_reason', _tf, None),
        'UserDefinedPeriod': ('user_defined_period', Period.parse, None),
        'PeriodStartDate': ('period_start_date', _dr, None), 'PeriodEndDate': ('period_end_date', _dr, None),
        'PeriodLength': ('period_length', _nr, None),
        'PlaceOfServiceCS': ('place_of_service_cs', _cs, None),
        'PlaceOfServiceLocation': ('place_of_service_location', to_integer, None),
    }
    return [(n,) + table[n] for n in names]


class ConditionEra(Criteria):
    TYPE = 'ConditionEra'
    _fields = _common('CodesetId', 'First', 'EraStartDate', 'EraEndDate', 'OccurrenceCount', 'EraLength',
                      'AgeAtStart', 'AgeAtEnd', 'Gender', 'GenderCS')


class ConditionOccurrence(Criteria):
    TYPE = 'ConditionOccurrence'
    _fields = _common('CodesetId', 'First', 'OccurrenceStartDate', 'OccurrenceEndDate') + [
        ('ConditionType', 'condition_type', _concepts, None), ('ConditionTypeCS', 'condition_type_cs', _cs, None),
        ('ConditionTypeExclude', 'condition_type_exclude', to_boolean, None)] + _common('StopReason') + [
        ('ConditionSourceConcept', 'condition_source_concept', to_integer, None)] + _common(
        'Age', 'Gender', 'GenderCS', 'ProviderSpecialty', 'ProviderSpecialtyCS', 'VisitType', 'VisitTypeCS') + [
        ('ConditionStatus', 'condition_status', _concepts, None),
        ('ConditionStatusCS', 'condition_status_cs', _cs, None)]


class Death(Criteria):
    TYPE = 'Death'
    _fields = _common('CodesetId', 'OccurrenceStartDate') + [
        ('DeathType', 'death_type', _concepts, None), ('DeathTypeCS', 'death_type_cs', _cs, None),
        ('DeathTypeExclude', 'death_type_exclude', to_bool, False),
        ('DeathSourceConcept', 'death_source_concept', to_integer, None)] + _common('Age', 'Gender', 'GenderCS')


class DeviceExposure(Criteria):
    TYPE = 'DeviceExposure'
    _fields = _common('CodesetId', 'First', 'OccurrenceStartDate', 'OccurrenceEndDate') + [
        ('DeviceType', 'device_type', _concepts, None), ('DeviceTypeCS', 'device_type_cs', _cs, None),
        ('DeviceTypeExclude', 'device_type_exclude', to_bool, False),
        ('UniqueDeviceId', 'unique_device_id', _tf, None)] + _common('Quantity') + [
        ('DeviceSourceConcept', 'device_source_concept', to_integer, None)] + _common(
        'Age', 'Gender', 'GenderCS', 'ProviderSpecialty', 'ProviderSpecialtyCS', 'VisitType', 'VisitTypeCS')


class DoseEra(Criteria):
    TYPE = 'DoseEra'
    _fields = _common('CodesetId', 'First', 'EraStartDate', 'EraEndDate', 'Unit', 'UnitCS') + [
        ('DoseValue', 'dose_value', _nr, None)] + _common('EraLength', 'AgeAtStart', 'AgeAtEnd', 'Gender', 'GenderCS')


class DrugEra(Criteria):
    TYPE = 'DrugEra'
    _fields = _common('CodesetId', 'First', 'EraStartDate', 'EraEndDate', 'OccurrenceCount') + [
        ('GapDays', 'gap_days', _nr, None)] + _common('EraLength', 'AgeAtStart', 'AgeAtEnd', 'Gender', 'GenderCS')


class DrugExposure(Criteria):
    TYPE = 'DrugExposure'
    _fields = _common('CodesetId', 'First', 'OccurrenceStartDate', 'OccurrenceEndDate') + [
        ('DrugType', 'drug_type', _concepts, None), ('DrugTypeCS', 'drug_type_cs', _cs, None),
        ('DrugTypeExclude', 'drug_type_exclude', to_bool, False)] + _common('StopReason') + [
        ('Refills', 'refills', _nr, None)] + _common('Quantity') + [
        ('DaysSupply', 'days_supply', _nr, None),
        ('RouteConcept', 'route_concept', _concepts, None), ('RouteConceptCS', 'route_concept_cs', _cs, None),
        ('EffectiveDrugDose', 'effective_drug_dose', _nr, None),
        ('DoseUnit', 'dose_unit', _concepts, None), ('DoseUnitCS', 'dose_unit_cs', _cs, None),
        ('LotNumber', 'lot_number', _tf, None),
        ('DrugSourceConcept', 'drug_source_concept', to_integer, None)] + _common(
        'Age', 'Gender', 'GenderCS', 'ProviderSpecialty', 'ProviderSpecialtyCS', 'VisitType', 'VisitTypeCS')


class CustomEra(Criteria):
    TYPE = 'CustomEra'
    _fields = [('CriteriaList', 'criteria_list', array_of(_criteria), None)] + _common('First') + [
        ('GapDays', 'gap_days', to_integer, None), ('StartDate', 'start_date', _dr, None),
        ('EndDate', 'end_date', _dr, None)] + _common('AgeAtStart', 'GenderCS') + [
        ('Duration', 'duration', _nr, None)]


class Episode(Criteria):
    TYPE = 'Episode'
    _fields = _common('CodesetId', 'First') + [
        ('EpisodeStartDate', 'episode_start_date', _dr, None), ('EpisodeEndDate', 'episode_end_date', _dr, None),
        ('EpisodeNumber', 'episode_number', _nr, None)] + _common('Age', 'GenderCS') + [
        ('EpisodeObjectConceptCS', 'episode_object_concept_cs', _cs, None),
        ('EpisodeTypeCS', 'episode_type_cs', _cs, None)]


class LocationRegion(Criteria):
    TYPE = 'LocationRegion'
    _fields = [('StartDate', 'start_date', _dr, None), ('EndDate', 'end_date', _dr, None)] + _common('CodesetId')


class Measurement(Criteria):
    TYPE = 'Measurement'
    _fields = _common('CodesetId', 'First', 'OccurrenceStartDate') + [
        ('MeasurementType', 'measurement_type', _concepts, None),
        ('MeasurementTypeCS', 'measurement_type_cs', _cs, None),
        ('MeasurementTypeExclude', 'measurement_type_exclude', to_bool, False),
        ('Operator', 'operator', _concepts, None), ('OperatorCS', 'operator_cs', _cs, None)] + _common(
        'ValueAsNumber', 'ValueAsConcept', 'ValueAsConceptCS', 'Unit', 'UnitCS') + [
        ('RangeLow', 'range_low', _nr, None), ('RangeHigh', 'range_high', _nr, None),
        ('RangeLowRatio', 'range_low_ratio', _nr, None), ('RangeHighRatio', 'range_high_ratio', _nr, None),
        ('Abnormal', 'abnormal', to_boolean, None),
        ('MeasurementSourceConcept', 'measurement_source_concept', to_integer, None)] + _common(
        'Age', 'Gender', 'GenderCS', 'ProviderSpecialty', 'ProviderSpecialtyCS', 'VisitType', 'VisitTypeCS')


class Observation(Criteria):
    TYPE = 'Observation'
    _fields = _common('CodesetId', 'First', 'OccurrenceStartDate') + [
        ('ObservationType', 'observation_type', _concepts, None),
        ('ObservationTypeCS', 'observation_type_cs', _cs, None),
        ('ObservationTypeExclude', 'observation_type_exclude', to_bool, False)] + _common('ValueAsNumber') + [
        ('ValueAsString', 'value_as_string', _tf, None)] + _common('ValueAsConcept', 'ValueAsConceptCS') + [
        ('Qualifier', 'qualifier', _concepts, None), ('QualifierCS', 'qualifier_cs', _cs, None)] + _common(
        'Unit', 'UnitCS') + [('ObservationSourceConcept', 'observation_source_concept', to_integer, None)] + _common(
        'Age', 'Gender', 'GenderCS', 'ProviderSpecialty', 'ProviderSpecialtyCS', 'VisitType', 'VisitTypeCS')


class ObservationPeriod(Criteria):
    TYPE = 'ObservationPeriod'
    _fields = _common('First', 'PeriodStartDate', 'PeriodEndDate', 'UserDefinedPeriod') + [
        ('PeriodType', 'period_type', _concepts, None), ('PeriodTypeCS', 'period_type_cs', _cs, None)] + _common(
        'PeriodLength', 'AgeAtStart', 'AgeAtEnd')


class PayerPlanPeriod(Criteria):
    TYPE = 'PayerPlanPeriod'
    _fields = _common('First', 'PeriodStartDate', 'PeriodEndDate', 'UserDefinedPeriod', 'PeriodLength',
                      'AgeAtStart', 'AgeAtEnd', 'Gender', 'GenderCS') + [
        (j, a, to_integer, None) for j, a in [
            ('PayerConcept', 'payer_concept'), ('PlanConcept', 'plan_concept'), ('SponsorConcept', 'sponsor_concept'),
            ('StopReasonConcept', 'stop_reason_concept'), ('PayerSourceConcept', 'payer_source_concept'),
            ('PlanSourceConcept', 'plan_source_concept'), ('SponsorSourceConcept', 'sponsor_source_concept'),
            ('StopReasonSourceConcept', 'stop_reason_source_concept')]]


class ProcedureOccurrence(Criteria):
    TYPE = 'ProcedureOccurrence'
    _fields = _common('CodesetId', 'First', 'OccurrenceStartDate') + [
        ('ProcedureType', 'procedure_type', _concepts, None), ('ProcedureTypeCS', 'procedure_type_cs', _cs, None),
        ('ProcedureTypeExclude', 'procedure_type_exclude', to_bool, False),
        ('Modifier', 'modifier', _concepts, None), ('ModifierCS', 'modifier_cs', _cs, None)] + _common('Quantity') + [
        ('ProcedureSourceConcept', 'procedure_source_concept', to_integer, None)] + _common(
        'Age', 'Gender', 'GenderCS', 'ProviderSpecialty', 'ProviderSpecialtyCS', 'VisitType', 'VisitTypeCS')


class Specimen(Criteria):
    TYPE = 'Specimen'
    _fields = _common('CodesetId', 'First', 'OccurrenceStartDate') + [
        ('SpecimenType', 'specimen_type', _concepts, None), ('SpecimenTypeCS', 'specimen_type_cs', _cs, None),
        ('SpecimenTypeExclude', 'specimen_type_exclude', to_bool, False)] + _common('Quantity', 'Unit', 'UnitCS') + [
        ('AnatomicSite', 'anatomic_site', _concepts, None), ('AnatomicSiteCS', 'anatomic_site_cs', _cs, None),
        ('DiseaseStatus', 'disease_status', _concepts, None), ('DiseaseStatusCS', 'disease_status_cs', _cs, None),
        ('SourceId', 'source_id', _tf, None),
        ('SpecimenSourceConcept', 'specimen_source_concept', to_integer, None)] + _common('Age', 'Gender', 'GenderCS')


class VisitDetail(Criteria):
    TYPE = 'VisitDetail'
    _fields = _common('CodesetId', 'First') + [
        ('VisitDetailStartDate', 'visit_detail_start_date', _dr, None),
        ('VisitDetailEndDate', 'visit_detail_end_date', _dr, None),
        ('VisitDetailTypeCS', 'visit_detail_type_cs', _cs, None),
        ('VisitDetailSourceConcept', 'visit_detail_source_concept', to_integer, None),
        ('VisitDetailLength', 'visit_detail_length', _nr, None)] + _common(
        'Age', 'GenderCS', 'ProviderSpecialtyCS', 'PlaceOfServiceCS', 'PlaceOfServiceLocation')


class VisitOccurrence(Criteria):
    TYPE = 'VisitOccurrence'
    _fields = _common('CodesetId', 'First', 'OccurrenceStartDate', 'OccurrenceEndDate', 'VisitType', 'VisitTypeCS') + [
        ('VisitTypeExclude', 'visit_type_exclude', to_bool, False),
        ('VisitSourceConcept', 'visit_source_concept', to_integer, None),
        ('VisitLength', 'visit_length', _nr, None)] + _common(
        'Age', 'Gender', 'GenderCS', 'ProviderSpecialty', 'ProviderSpecialtyCS') + [
        ('PlaceOfService', 'place_of_service', _concepts, None)] + _common('PlaceOfServiceCS', 'PlaceOfServiceLocation')


CRITERIA_TYPES = {c.TYPE: c for c in (ConditionEra, ConditionOccurrence, Death, DeviceExposure, DoseEra, DrugEra,
                                      DrugExposure, CustomEra, Episode, LocationRegion, Measurement, Observation,
                                      ObservationPeriod, ProcedureOccurrence, Specimen, VisitOccurrence, VisitDetail,
                                      PayerPlanPeriod)}


def _wrapper(types, what):
    """@JsonTypeInfo(WRAPPER_OBJECT): {"TypeName": {...}} — exactly one key."""
    def parse(v):
        if v is None:
            return None
        if not isinstance(v, dict) or len(v) != 1:
            raise CirceError(f'MismatchedInputException: expected wrapper object for {what}')
        (name, body), = v.items()
        cls = types.get(name)
        if cls is None:
            raise CirceError(f"InvalidTypeIdException: Could not resolve type id '{name}' as a subtype of {what}")
        if body is None:
            return None
        return cls.parse(body)
    return parse


parse_criteria = _wrapper(CRITERIA_TYPES, 'Criteria')


class DateOffsetStrategy(Bean):
    TYPE = 'DateOffset'
    _fields = [('DateField', 'date_field', enum_of(['StartDate', 'EndDate']), 'StartDate'), ('Offset', 'offset', to_int, 0)]


class CustomEraStrategy(Bean):
    TYPE = 'CustomEra'
    _fields = [('DrugCodesetId', 'drug_codeset_id', to_integer, None), ('GapDays', 'gap_days', to_int, 0),
               ('Offset', 'offset', to_int, 0), ('DaysSupplyOverride', 'days_supply_override', to_integer, None)]


parse_end_strategy = _wrapper({'DateOffset': DateOffsetStrategy, 'CustomEra': CustomEraStrategy}, 'EndStrategy')


class ObservationFilter(Bean):
    _fields = [('PriorDays', 'prior_days', to_int, 0), ('PostDays', 'post_days', to_int, 0)]


class ResultLimit(Bean):
    _fields = [('Type', 'type', to_str, 'First')]


class PrimaryCriteria(Bean):
    _fields = [('CriteriaList', 'criteria_list', array_of(_criteria), list),
               ('ObservationWindow', 'observation_window', ObservationFilter.parse, None),
               ('PrimaryCriteriaLimit', 'primary_limit', ResultLimit.parse, ResultLimit)]


class InclusionRule(Bean):
    _fields = [('name', 'name', to_str, None), ('description', 'description', to_str, None),
               ('expression', 'expression', _group, None)]


class CollapseSettings(Bean):
    _fields = [('CollapseType', 'collapse_type', enum_of(['ERA']), 'ERA'), ('EraPad', 'era_pad', to_int, 0)]


class CohortExpression(Bean):
    _fields = [('cdmVersionRange', 'cdm_version_range', to_str, None), ('Title', 'title', to_str, None),
               ('PrimaryCriteria', 'primary_criteria', PrimaryCriteria.parse, None),
               ('AdditionalCriteria', 'additional_criteria', _group, None),
               ('ConceptSets', 'concept_sets', array_of(ConceptSet.parse), None),
               ('QualifiedLimit', 'qualified_limit', ResultLimit.parse, ResultLimit),
               ('ExpressionLimit', 'expression_limit', ResultLimit.parse, ResultLimit),
               ('InclusionRules', 'inclusion_rules', array_of(InclusionRule.parse), list),
               ('EndStrategy', 'end_strategy', parse_end_strategy, None),
               ('CensoringCriteria', 'censoring_criteria', array_of(_criteria), None),
               ('CollapseSettings', 'collapse_settings', CollapseSettings.parse, CollapseSettings),
               ('CensorWindow', 'censor_window', Period.parse, None)]

    @classmethod
    def from_json(cls, text):
        if isinstance(text, (bytes, str)):
            try:
                v = json.loads(text)
            except ValueError as e:
                raise CirceError(f'JsonParseException: {e}')
        else:
            v = text
        if v is None:
            return None
        return cls.parse(v)
