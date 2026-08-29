"""
Data Quality Resolution Engine — hardened classification with finding codes.

ARCHITECTURE:
  finding_code → resolution rule (authoritative)
  Human-readable messages are for display ONLY.
  Root cause detection uses structured entity/type/id, NOT message text.
  Dependency graph drives ROOT_CAUSE vs DEPENDENT classification.

RESOLUTION WORKFLOW:
  1. classify_findings() → classification dict
  2. ResolutionApplier.preview() → audit trail (no mutations)
  3. ResolutionApplier.apply() → mutations on migration representation (NOT legacy DB)
  4. Re-run classification to verify

This engine never modifies the legacy SQLite database.
All mutations apply to the in-memory migration candidate representation.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from inventory.migration_engine import FindingCode, Severity


# ============================================================
# RESOLUTION CATEGORIES
# ============================================================

class Resolution:
    AUTO_FIX_SAFE = 'AUTO_FIX_SAFE'
    MANUAL_REVIEW = 'MANUAL_REVIEW'
    STRUCTURAL_PROBLEM = 'STRUCTURAL_PROBLEM'
    MIGRATION_BLOCKER = 'MIGRATION_BLOCKER'
    ACCEPTED_EXCEPTION = 'ACCEPTED_EXCEPTION'


# ============================================================
# CLASSIFICATION RULES: finding_code → resolution
# ============================================================

CLASSIFICATION_RULES = {
    # Category
    FindingCode.CATEGORY_EMPTY: Resolution.MIGRATION_BLOCKER,
    FindingCode.CATEGORY_TEST_DATA: Resolution.ACCEPTED_EXCEPTION,
    FindingCode.CATEGORY_DUPLICATE: Resolution.MANUAL_REVIEW,

    # Supplier
    FindingCode.SUPPLIER_EMPTY: Resolution.MIGRATION_BLOCKER,
    FindingCode.SUPPLIER_DUPLICATE: Resolution.MANUAL_REVIEW,

    # Product
    FindingCode.PRODUCT_CATEGORY_MISSING: Resolution.MIGRATION_BLOCKER,
    FindingCode.PRODUCT_CATEGORY_INVALID: Resolution.ACCEPTED_EXCEPTION,
    FindingCode.PRODUCT_DUPLICATE_SKU: Resolution.MANUAL_REVIEW,
    FindingCode.PRODUCT_NAME_EMPTY: Resolution.MIGRATION_BLOCKER,

    # Batch
    FindingCode.BATCH_INVALID_PRODUCT: Resolution.MIGRATION_BLOCKER,
    FindingCode.BATCH_WEIGHT_ZERO: Resolution.ACCEPTED_EXCEPTION,
    FindingCode.BATCH_MISSING_SUPPLIER: Resolution.MANUAL_REVIEW,
    FindingCode.BATCH_INVALID_LOT: Resolution.MANUAL_REVIEW,

    # Package
    FindingCode.PACKAGE_ORPHAN_PRODUCT: Resolution.MIGRATION_BLOCKER,
    FindingCode.PACKAGE_UNKNOWN_STORAGE_STATUS: Resolution.MIGRATION_BLOCKER,
    FindingCode.PACKAGE_STATE_CONFLICT: Resolution.MANUAL_REVIEW,
    FindingCode.PACKAGE_DUPLICATE_BARCODE: Resolution.MIGRATION_BLOCKER,
    FindingCode.PACKAGE_EMPTY_BARCODE: Resolution.MIGRATION_BLOCKER,
    FindingCode.PACKAGE_INVALID_WEIGHT: Resolution.MIGRATION_BLOCKER,
    FindingCode.PACKAGE_NEGATIVE_PRICE: Resolution.MANUAL_REVIEW,
    FindingCode.PACKAGE_DUPLICATE_LOYVERSE_SKU: Resolution.MANUAL_REVIEW,
}

RULE_DESCRIPTIONS = {
    FindingCode.CATEGORY_EMPTY: 'Cannot create Category without name',
    FindingCode.CATEGORY_TEST_DATA: 'SKIP — test data, do not migrate',
    FindingCode.CATEGORY_DUPLICATE: 'Duplicate category code — need human decision',
    FindingCode.SUPPLIER_EMPTY: 'Cannot create Supplier without name',
    FindingCode.SUPPLIER_DUPLICATE: 'Duplicate supplier name — need human decision',
    FindingCode.PRODUCT_CATEGORY_MISSING: 'Cannot create Product without Category',
    FindingCode.PRODUCT_CATEGORY_INVALID: 'Category is test data — skip product (accepted exception)',
    FindingCode.PRODUCT_DUPLICATE_SKU: 'Duplicate SKU — need decision: merge or assign new SKU',
    FindingCode.PRODUCT_NAME_EMPTY: 'Cannot create Product without name',
    FindingCode.BATCH_INVALID_PRODUCT: 'Batch references invalid product — cannot create',
    FindingCode.BATCH_WEIGHT_ZERO: 'Product_info.weight=0.0 — not used for Package weight',
    FindingCode.BATCH_MISSING_SUPPLIER: 'Missing supplier — requires business decision',
    FindingCode.BATCH_INVALID_LOT: 'Invalid lot number — needs review',
    FindingCode.PACKAGE_ORPHAN_PRODUCT: 'Package references invalid product chain',
    FindingCode.PACKAGE_UNKNOWN_STORAGE_STATUS: 'Unmapped storage_status — need decision',
    FindingCode.PACKAGE_STATE_CONFLICT: 'Conflicting state fields — need human decision',
    FindingCode.PACKAGE_DUPLICATE_BARCODE: 'Duplicate barcode — physical package identity conflict',
    FindingCode.PACKAGE_EMPTY_BARCODE: 'Package must have a barcode',
    FindingCode.PACKAGE_INVALID_WEIGHT: 'Zero or negative weight — cannot create Package',
    FindingCode.PACKAGE_NEGATIVE_PRICE: 'Negative price — need review',
    FindingCode.PACKAGE_DUPLICATE_LOYVERSE_SKU: 'Duplicate Loyverse SKU — need review',
}


# ============================================================
# FINDING DATA CLASS
# ============================================================

@dataclass
class Finding:
    finding_code: str
    entity: str
    legacy_id: int
    resolution: str
    rule: str
    message: str
    severity: str = 'ERROR'
    root_cause_code: Optional[str] = None
    root_cause_entity: Optional[str] = None
    root_cause_legacy_id: Optional[int] = None
    depends_on_codes: list = field(default_factory=list)
    affected_records: int = 1
    evidence: str = ''


# ============================================================
# DEPENDENCY GRAPH
# ============================================================

DEPENDENCY_GRAPH = {
    FindingCode.PRODUCT_CATEGORY_MISSING: {
        'resolves': [
            FindingCode.BATCH_INVALID_PRODUCT,
            FindingCode.PACKAGE_ORPHAN_PRODUCT,
        ],
        'description': 'Product without category → downstream Batch and Package cannot be created',
    },
    FindingCode.PRODUCT_CATEGORY_INVALID: {
        'resolves': [
            FindingCode.BATCH_INVALID_PRODUCT,
            FindingCode.PACKAGE_ORPHAN_PRODUCT,
        ],
        'description': 'Product with invalid category (test data) → downstream Batch and Package excluded',
    },
    FindingCode.PRODUCT_NAME_EMPTY: {'resolves': []},
    FindingCode.BATCH_INVALID_PRODUCT: {'resolves': []},
    FindingCode.PACKAGE_ORPHAN_PRODUCT: {'resolves': []},
    FindingCode.PACKAGE_UNKNOWN_STORAGE_STATUS: {'resolves': []},
    FindingCode.CATEGORY_EMPTY: {'resolves': []},
    FindingCode.CATEGORY_TEST_DATA: {'resolves': []},
    FindingCode.CATEGORY_DUPLICATE: {'resolves': []},
    FindingCode.SUPPLIER_EMPTY: {'resolves': []},
    FindingCode.SUPPLIER_DUPLICATE: {'resolves': []},
    FindingCode.PRODUCT_DUPLICATE_SKU: {'resolves': []},
    FindingCode.BATCH_WEIGHT_ZERO: {'resolves': []},
    FindingCode.BATCH_MISSING_SUPPLIER: {'resolves': []},
    FindingCode.BATCH_INVALID_LOT: {'resolves': []},
    FindingCode.PACKAGE_STATE_CONFLICT: {'resolves': []},
    FindingCode.PACKAGE_DUPLICATE_BARCODE: {'resolves': []},
    FindingCode.PACKAGE_EMPTY_BARCODE: {'resolves': []},
    FindingCode.PACKAGE_INVALID_WEIGHT: {'resolves': []},
    FindingCode.PACKAGE_NEGATIVE_PRICE: {'resolves': []},
    FindingCode.PACKAGE_DUPLICATE_LOYVERSE_SKU: {'resolves': []},
}


# ============================================================
# CLASSIFICATION ENGINE
# ============================================================

def classify_findings(results):
    """
    Classify dry-run findings with stable codes, root causes, and dependencies.

    Returns:
        dict: {findings: [Finding], summary: dict, root_causes: list, dependency_chains: list}
    """
    findings = []

    for model_key in ['categories', 'suppliers', 'products', 'batches', 'packages']:
        for candidate in results.get(model_key, []):
            for issue in candidate.issues:
                finding_code = issue.code
                if not finding_code:
                    finding_code = 'UNKNOWN'
                resolution = CLASSIFICATION_RULES.get(finding_code, Resolution.MANUAL_REVIEW)
                rule = RULE_DESCRIPTIONS.get(finding_code, 'Unknown — needs review')
                f = Finding(
                    finding_code=finding_code,
                    entity=candidate.target_model,
                    legacy_id=candidate.legacy_id,
                    resolution=resolution,
                    rule=rule,
                    message=issue.message,
                    severity=issue.severity,
                )
                findings.append(f)

    candidate_map = {}
    for model_key in ['categories', 'suppliers', 'products', 'batches', 'packages']:
        for c in results.get(model_key, []):
            candidate_map[(c.target_model, c.legacy_id)] = c

    _classify_root_and_dependents(findings, candidate_map)

    root_causes_dedup = []
    seen_rc = set()
    for f in findings:
        if not f.depends_on_codes:
            dep_graph_entry = DEPENDENCY_GRAPH.get(f.finding_code, {})
            if dep_graph_entry.get('resolves'):
                key = (f.entity, f.legacy_id, f.finding_code)
                if key not in seen_rc:
                    seen_rc.add(key)
                    root_causes_dedup.append(f)

    summary = {
        'total_findings': len(findings),
        'auto_fix_safe': sum(1 for f in findings if f.resolution == Resolution.AUTO_FIX_SAFE),
        'manual_review': sum(1 for f in findings if f.resolution == Resolution.MANUAL_REVIEW),
        'structural_problem': sum(1 for f in findings if f.resolution == Resolution.STRUCTURAL_PROBLEM),
        'migration_blocker': sum(1 for f in findings if f.resolution == Resolution.MIGRATION_BLOCKER),
        'accepted_exception': sum(1 for f in findings if f.resolution == Resolution.ACCEPTED_EXCEPTION),
        'root_causes': len(root_causes_dedup),
        'dependent_findings': sum(1 for f in findings if f.depends_on_codes),
    }

    dependency_chains = _build_dependency_chains(findings, candidate_map)

    return {
        'findings': findings,
        'summary': summary,
        'root_causes': root_causes_dedup,
        'dependency_chains': dependency_chains,
    }


def _classify_root_and_dependents(findings, candidate_map):
    product_root_causes = {}
    for f in findings:
        if f.entity == 'Product' and f.finding_code in (
            FindingCode.PRODUCT_CATEGORY_MISSING,
            FindingCode.PRODUCT_CATEGORY_INVALID,
        ):
            product_root_causes[(f.entity, f.legacy_id)] = f.finding_code
            product_root_causes[(f.entity, int(f.legacy_id))] = f.finding_code

    for f in findings:
        if f.entity == 'Batch' and f.finding_code == FindingCode.BATCH_INVALID_PRODUCT:
            c = candidate_map.get((f.entity, f.legacy_id))
            if c:
                product_legacy_id = c.data.get('product_legacy_id')
                if product_legacy_id is not None:
                    root_code = (product_root_causes.get(('Product', product_legacy_id))
                                or product_root_causes.get(('Product', int(product_legacy_id))
                                    if str(product_legacy_id).isdigit() else None))
                    if root_code:
                        f.root_cause_code = root_code
                        f.root_cause_entity = 'Product'
                        f.root_cause_legacy_id = product_legacy_id
                        f.depends_on_codes = [root_code]

        elif f.entity == 'Package' and f.finding_code == FindingCode.PACKAGE_ORPHAN_PRODUCT:
            c = candidate_map.get((f.entity, f.legacy_id))
            if c:
                meat_parts_id = c.data.get('meat_parts_id')
                product_legacy_id = c.data.get('product_legacy_id')
                lookup_id = meat_parts_id if meat_parts_id is not None else product_legacy_id
                if lookup_id is not None:
                    root_code = (product_root_causes.get(('Product', lookup_id))
                                or product_root_causes.get(('Product', int(lookup_id))
                                    if str(lookup_id).isdigit() else None))
                    if root_code:
                        f.root_cause_code = root_code
                        f.root_cause_entity = 'Product'
                        f.root_cause_legacy_id = lookup_id
                        f.depends_on_codes = [root_code]


def _build_dependency_chains(findings, candidate_map):
    chains = []
    for f in findings:
        if f.depends_on_codes and f.root_cause_entity:
            chains.append({
                'root': {
                    'entity': f.root_cause_entity,
                    'legacy_id': f.root_cause_legacy_id,
                    'finding_code': f.root_cause_code,
                },
                'dependent': {
                    'entity': f.entity,
                    'legacy_id': f.legacy_id,
                    'finding_code': f.finding_code,
                },
            })
    return chains


# ============================================================
# AUDIT TRAIL
# ============================================================

@dataclass
class AuditEntry:
    rule_id: str
    entity: str
    legacy_id: int
    field: str
    old_value: str
    new_value: str
    reason: str
    evidence: str
    requires_approval: bool
    applied: bool = False
    timestamp: str = ''


class AuditTrail:
    """Immutable audit trail for resolution mutations."""

    def __init__(self):
        self.entries = []
        self._applied_count = 0

    def add(self, rule_id, entity, legacy_id, field, old_value, new_value,
            reason, evidence, requires_approval):
        entry = AuditEntry(
            rule_id=rule_id,
            entity=entity,
            legacy_id=legacy_id,
            field=field,
            old_value=str(old_value),
            new_value=str(new_value),
            reason=reason,
            evidence=evidence,
            requires_approval=requires_approval,
        )
        self.entries.append(entry)
        return entry

    def apply_entry(self, entry):
        entry.applied = True
        entry.timestamp = datetime.now().isoformat()
        self._applied_count += 1

    @property
    def applied_count(self):
        return self._applied_count

    @property
    def pending_count(self):
        return sum(1 for e in self.entries if not e.applied)

    def get_applied(self):
        return [e for e in self.entries if e.applied]

    def get_pending(self):
        return [e for e in self.entries if not e.applied]


# ============================================================
# APPROVED RESOLUTION RULES
# ============================================================

# Each rule maps a (rule_id) → how to resolve it.
# Only rules listed here are applied. Everything else is MANUAL_REVIEW.
APPROVED_RULES = {
    'RESOLVE_PRODUCT_10_CATEGORY': {
        'description': 'Assign CHICKEN category to Product #10 (ปีกกลาง)',
        'entity': 'Product',
        'legacy_id': 10,
        'finding_code': FindingCode.PRODUCT_CATEGORY_MISSING,
        'evidence': 'product name="ปีกกลาง" (chicken mid-wing), barcode_prefix=1002, prefix pattern 10xx=chicken',
        'changes': [
            {
                'field': 'category_code',
                'old_value': None,
                'new_value': 'CHICKEN',
                'reason': 'Derived from product name and barcode prefix pattern',
            },
            {
                'field': 'category_legacy_id',
                'old_value': None,
                'new_value': '2',
                'reason': 'CHICKEN category legacy_id=2 in legacy database',
            },
        ],
        'resolves_findings': [
            FindingCode.PRODUCT_CATEGORY_MISSING,
            FindingCode.BATCH_INVALID_PRODUCT,
            FindingCode.PACKAGE_ORPHAN_PRODUCT,
        ],
    },
    'RESOLVE_PENDING_TO_PACKED': {
        'description': 'Map storage_status "pending" → PACKED state for 20 packages',
        'entity': 'Package',
        'legacy_id': None,  # applies to all matching packages
        'finding_code': FindingCode.PACKAGE_UNKNOWN_STORAGE_STATUS,
        'evidence': 'freeze_started=None, thaw_started=None, thaw_queue=0, activated=1, recent mfg dates',
        'changes': [
            {
                'field': 'canonical_state',
                'old_value': None,  # was unmapped
                'new_value': 'PACKED',
                'reason': 'storage_status "pending" means packed but not yet frozen',
            },
        ],
        'resolves_findings': [
            FindingCode.PACKAGE_UNKNOWN_STORAGE_STATUS,
        ],
    },
    'RESOLVE_DUPLICATE_SKU_PENDING': {
        'description': 'Record pending SKU assignments for duplicate pairs (requires final SKU values)',
        'entity': 'Product',
        'legacy_id': None,  # applies to all duplicate SKU products
        'finding_code': FindingCode.PRODUCT_DUPLICATE_SKU,
        'evidence': 'MP-1108: #3 vs #6 (different products), MP-8206: #7 vs #22 (different products)',
        'changes': [],  # no data mutation — only records the decision
        'resolves_findings': [],
    },
}


# ============================================================
# RESOLUTION APPLIER
# ============================================================

class ResolutionApplier:
    """
    Applies approved resolution rules to migration candidate representation.

    Never modifies the legacy database.
    All mutations are on the in-memory Candidate objects.
    Produces an audit trail for every proposed/applied mutation.
    """

    def __init__(self):
        self.audit = AuditTrail()

    def preview(self, results):
        """
        Generate audit trail entries for all approved resolutions.
        Read-only — does not modify any candidates.
        Returns AuditTrail with proposed entries.
        """
        trail = AuditTrail()

        # ── Rule 1: Product #10 → CHICKEN ──
        self._preview_product_category(results, trail)

        # ── Rule 2: pending → PACKED ──
        self._preview_pending_packages(results, trail)

        # ── Rule 3: Duplicate SKU decisions ──
        self._preview_duplicate_sku(results, trail)

        return trail

    def apply(self, results, trail):
        """
        Apply all approved entries from the audit trail.
        Mutates Candidate objects in-place.
        Returns the count of applied entries.
        """
        applied = 0
        for entry in trail.get_pending():
            if entry.requires_approval:
                continue

            success = False
            if entry.rule_id == 'RESOLVE_PRODUCT_10_CATEGORY':
                success = self._apply_product_category(results, entry)
            elif entry.rule_id == 'RESOLVE_PENDING_TO_PACKED':
                success = self._apply_pending_to_packed(results, entry)
            elif entry.rule_id == 'RESOLVE_DUPLICATE_SKU_PENDING':
                success = True  # decision recorded, no mutation needed

            if success:
                trail.apply_entry(entry)
                applied += 1

        return applied

    def _preview_product_category(self, results, trail):
        """Preview: Product #10 → CHICKEN category."""
        rule = APPROVED_RULES['RESOLVE_PRODUCT_10_CATEGORY']
        for c in results.get('products', []):
            if c.legacy_id == 10 or str(c.legacy_id) == '10':
                for change in rule['changes']:
                    old_val = c.data.get(change['field'])
                    trail.add(
                        rule_id='RESOLVE_PRODUCT_10_CATEGORY',
                        entity='Product',
                        legacy_id=c.legacy_id,
                        field=change['field'],
                        old_value=old_val,
                        new_value=change['new_value'],
                        reason=change['reason'],
                        evidence=rule['evidence'],
                        requires_approval=False,
                    )
                break

    def _preview_pending_packages(self, results, trail):
        """Preview: storage_status 'pending' → PACKED."""
        rule = APPROVED_RULES['RESOLVE_PENDING_TO_PACKED']
        for c in results.get('packages', []):
            # Find packages that are SKIPPED due to unknown storage_status
            if c.status == 'SKIPPED':
                for issue in c.issues:
                    if issue.code == FindingCode.PACKAGE_UNKNOWN_STORAGE_STATUS:
                        change = rule['changes'][0]
                        trail.add(
                            rule_id='RESOLVE_PENDING_TO_PACKED',
                            entity='Package',
                            legacy_id=c.legacy_id,
                            field=change['field'],
                            old_value=c.data.get(change['field'], '(unmapped)'),
                            new_value=change['new_value'],
                            reason=change['reason'],
                            evidence=rule['evidence'],
                            requires_approval=False,
                        )
                        break

    def _preview_duplicate_sku(self, results, trail):
        """Preview: record duplicate SKU decisions (no mutation, requires final SKU)."""
        rule = APPROVED_RULES['RESOLVE_DUPLICATE_SKU_PENDING']
        seen_skus = {}
        for c in results.get('products', []):
            if c.status == 'WARNING':
                for issue in c.issues:
                    if issue.code == FindingCode.PRODUCT_DUPLICATE_SKU:
                        sku = c.data.get('sku', '')
                        if sku not in seen_skus:
                            seen_skus[sku] = []
                        seen_skus[sku].append(c.legacy_id)

        for sku, legacy_ids in sorted(seen_skus.items()):
            if len(legacy_ids) > 1:
                trail.add(
                    rule_id='RESOLVE_DUPLICATE_SKU_PENDING',
                    entity='Product',
                    legacy_id=legacy_ids[0],
                    field='sku',
                    old_value=sku,
                    new_value=f'{sku} (requires final SKU assignment)',
                    reason=f'Products {legacy_ids} share SKU {sku} — different products, need new SKU',
                    evidence=rule['evidence'],
                    requires_approval=True,  # needs final SKU value
                )

    def _apply_product_category(self, results, entry):
        """Apply: Product #10 → CHICKEN category. Cascade to downstream Batch/Package."""
        from inventory.migration_engine import Status
        for c in results.get('products', []):
            if (c.legacy_id == 10 or str(c.legacy_id) == '10'):
                if entry.field == 'category_code':
                    c.data['category_code'] = entry.new_value
                elif entry.field == 'category_legacy_id':
                    c.data['category_legacy_id'] = entry.new_value
                # If product was SKIPPED due to missing category, upgrade to VALID
                if c.status == Status.SKIPPED:
                    c.issues = [i for i in c.issues
                               if i.code != FindingCode.PRODUCT_CATEGORY_MISSING]
                    if not c.issues:
                        c.status = Status.VALID

                # ── CASCADE: Upgrade downstream Batch and Package candidates ──
                if c.status == Status.VALID:
                    self._cascade_product_fix(results, c.legacy_id)
                return True
        return False

    def _cascade_product_fix(self, results, product_legacy_id):
        """After fixing a Product, upgrade downstream Batch/Package from SKIPPED to VALID."""
        from inventory.migration_engine import Status

        # Upgrade Batch candidates that reference this product
        for bc in results.get('batches', []):
            bc_product_id = bc.data.get('product_legacy_id')
            if bc_product_id is not None and (bc_product_id == product_legacy_id or str(bc_product_id) == str(product_legacy_id)):
                if bc.status == Status.SKIPPED:
                    bc.issues = [i for i in bc.issues
                                if i.code != FindingCode.BATCH_INVALID_PRODUCT]
                    if not bc.issues:
                        bc.status = Status.VALID

        # Upgrade Package candidates that reference this product
        for pc in results.get('packages', []):
            pc_product_id = pc.data.get('meat_parts_id') or pc.data.get('product_legacy_id')
            if pc_product_id is not None and (pc_product_id == product_legacy_id or str(pc_product_id) == str(product_legacy_id)):
                if pc.status == Status.SKIPPED:
                    pc.issues = [i for i in pc.issues
                                if i.code != FindingCode.PACKAGE_ORPHAN_PRODUCT]
                    if not pc.issues:
                        pc.status = Status.VALID

    def _apply_pending_to_packed(self, results, entry):
        """Apply: storage_status 'pending' → PACKED."""
        for c in results.get('packages', []):
            if (c.legacy_id == entry.legacy_id or str(c.legacy_id) == str(entry.legacy_id)):
                if entry.field == 'canonical_state':
                    c.data['canonical_state'] = entry.new_value
                # If package was SKIPPED due to unknown status, upgrade to VALID
                if c.status == 'SKIPPED':
                    c.issues = [i for i in c.issues
                               if i.code != FindingCode.PACKAGE_UNKNOWN_STORAGE_STATUS]
                    if not c.issues:
                        from inventory.migration_engine import Status
                        c.status = Status.VALID
                return True
        return False


# ============================================================
# PROVISIONAL MAPPINGS (not yet approved)
# ============================================================

PROVISIONAL_MAPPINGS = {
    'product_10_to_chicken': {
        'description': 'Assign Category CHICKEN to Product #10 (ปีกกลาง)',
        'confidence': 'HIGH',
        'status': 'APPROVED',
        'requires_business_confirmation': False,
        'evidence': [
            'product name = "ปีกกลาง" (chicken mid-wing)',
            'barcode_prefix = 1002',
            'prefix pattern: 10xx = chicken products',
        ],
        'affected_finding_code': FindingCode.PRODUCT_CATEGORY_MISSING,
    },
    'pending_to_packed': {
        'description': 'Map storage_status "pending" → PACKED state',
        'confidence': 'HIGH',
        'status': 'APPROVED',
        'requires_business_confirmation': False,
        'evidence': [
            'freeze_started=None (never frozen)',
            'thaw_started=None (never thawed)',
            'thaw_queue_position=0 (never in queue)',
            'activated=1 (in use)',
            'mfg dates are recent',
        ],
        'affected_finding_code': FindingCode.PACKAGE_UNKNOWN_STORAGE_STATUS,
    },
    'assign_new_skus': {
        'description': 'Assign new SKUs for duplicate pairs instead of merging',
        'confidence': 'HIGH',
        'status': 'PENDING_FINAL_SKU',
        'requires_business_confirmation': True,
        'evidence': [
            'MP-1108: meat_parts #3 vs #6 — different products',
            'MP-8206: meat_parts #7 vs #22 — different products',
        ],
        'affected_finding_code': FindingCode.PRODUCT_DUPLICATE_SKU,
    },
}


# ============================================================
# REPORT PRINTER
# ============================================================

def print_resolution_report(classification):
    """Print a human-readable resolution report with dependency info."""
    s = classification['summary']
    findings = classification['findings']
    root_causes = classification['root_causes']

    print()
    print('=' * 60)
    print('DATA QUALITY RESOLUTION REPORT (HARDENED)')
    print('=' * 60)
    print(f'  Total findings:         {s["total_findings"]}')
    print(f'  AUTO_FIX_SAFE:          {s["auto_fix_safe"]}')
    print(f'  MANUAL_REVIEW:          {s["manual_review"]}')
    print(f'  STRUCTURAL_PROBLEM:     {s["structural_problem"]}')
    print(f'  MIGRATION_BLOCKER:      {s["migration_blocker"]}')
    print(f'  ACCEPTED_EXCEPTION:     {s["accepted_exception"]}')
    print(f'  Root causes:            {s["root_causes"]}')
    print(f'  Dependent findings:     {s["dependent_findings"]}')
    print()

    if root_causes:
        print('-' * 60)
        print('  ROOT CAUSES AND DEPENDENCY CHAINS')
        print('-' * 60)
        for rc in root_causes:
            print(f'  🔴 {rc.finding_code}')
            print(f'     Entity: {rc.entity} #{rc.legacy_id}')
            print(f'     Evidence: {rc.evidence}')
            dependents = [f for f in findings
                         if rc.entity in (f.root_cause_entity or '')
                         and str(rc.legacy_id) == str(f.root_cause_legacy_id)]
            if dependents:
                print(f'     ↓ Dependent findings ({len(dependents)}):')
                for d in dependents:
                    print(f'       → {d.finding_code} ({d.entity} #{d.legacy_id})')
            print()

    by_resolution = {}
    for f in findings:
        by_resolution.setdefault(f.resolution, []).append(f)

    for resolution in [Resolution.MIGRATION_BLOCKER, Resolution.MANUAL_REVIEW,
                       Resolution.AUTO_FIX_SAFE, Resolution.ACCEPTED_EXCEPTION,
                       Resolution.STRUCTURAL_PROBLEM]:
        items = by_resolution.get(resolution, [])
        if not items:
            continue
        print('-' * 60)
        print(f'  {resolution} ({len(items)})')
        print('-' * 60)
        for f in items:
            label = f'#{f.legacy_id}'
            root_tag = f' [ROOT: {f.root_cause_code}]' if f.root_cause_code and not f.depends_on_codes else ''
            dep_tag = f' [DEPENDS ON: {",".join(f.depends_on_codes)}]' if f.depends_on_codes else ''
            print(f'    {f.entity:12s} {label}{root_tag}{dep_tag}')
            print(f'      Code: {f.finding_code}')
            print(f'      Rule: {f.rule}')
            if f.evidence:
                print(f'      Evidence: {f.evidence}')
            print()

    print('=' * 60)
    print('RESOLUTION COMPLETE — NO DATA WAS MODIFIED')
    print('=' * 60)
    print()


def print_audit_trail(trail):
    """Print a human-readable audit trail."""
    print()
    print('=' * 60)
    print('RESOLUTION AUDIT TRAIL')
    print('=' * 60)
    print(f'  Total entries:   {len(trail.entries)}')
    print(f'  Applied:         {trail.applied_count}')
    print(f'  Pending:         {trail.pending_count}')
    print()

    for entry in trail.entries:
        status = '✅ APPLIED' if entry.applied else '⏳ PENDING'
        approval = ' [REQUIRES APPROVAL]' if entry.requires_approval else ''
        print(f'  {status}{approval}')
        print(f'    Rule:      {entry.rule_id}')
        print(f'    Entity:    {entry.entity} #{entry.legacy_id}')
        print(f'    Field:     {entry.field}')
        print(f'    Old:       {entry.old_value}')
        print(f'    New:       {entry.new_value}')
        print(f'    Reason:    {entry.reason}')
        print(f'    Evidence:  {entry.evidence}')
        if entry.applied:
            print(f'    Applied:   {entry.timestamp}')
        print()

    print('=' * 60)
    if trail.pending_count > 0:
        print(f'  ⚠️  {trail.pending_count} entries require approval before applying')
    else:
        print('  ✅ All entries applied')
    print('=' * 60)
    print()
