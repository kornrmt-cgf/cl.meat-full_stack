"""
Data Quality Resolution Engine — hardened classification with finding codes.

ARCHITECTURE:
  finding_code → resolution rule (authoritative)
  Human-readable messages are for display ONLY.
  Root cause detection uses structured entity/type/id, NOT message text.
  Dependency graph drives ROOT_CAUSE vs DEPENDENT classification.

This engine is READ-ONLY. It never modifies source data.
"""
from dataclasses import dataclass, field
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

# This is the single source of truth for how each finding code is resolved.
# No message-string matching. No hardcoded entity references.
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
    FindingCode.BATCH_MISSING_SUPPLIER: Resolution.AUTO_FIX_SAFE,
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

# Human-readable rule descriptions for each finding code
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
    FindingCode.BATCH_MISSING_SUPPLIER: 'Missing supplier — use placeholder',
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
    # Structured root cause reference (by finding_code, NOT by message text)
    root_cause_code: Optional[str] = None
    root_cause_entity: Optional[str] = None
    root_cause_legacy_id: Optional[int] = None
    depends_on_codes: list = field(default_factory=list)
    affected_records: int = 1
    evidence: str = ''


# ============================================================
# DEPENDENCY GRAPH
# ============================================================

# When a root cause finding_code is resolved, these dependent finding_codes are also resolved.
# This is the AUTHORITATIVE source for dependency relationships.
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
    FindingCode.PRODUCT_NAME_EMPTY: {
        'resolves': [],
        'description': 'Product without name — no downstream (products are leaf-level)',
    },
    # Leaf nodes — no downstream
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

    ARCHITECTURE:
    1. Every issue carries a stable `code` (FindingCode) — set by migration_engine.
    2. This engine maps finding_code → resolution using CLASSIFICATION_RULES.
    3. Root cause detection uses structured entity/type/id from the Candidate, NOT message text.
    4. Dependency graph drives ROOT_CAUSE vs DEPENDENT classification.

    Returns:
        dict: {findings: [Finding], summary: dict, root_causes: list, dependency_chains: list}
    """
    findings = []
    seen_root_causes = {}  # (entity, legacy_id) → Finding (dedup)

    for model_key in ['categories', 'suppliers', 'products', 'batches', 'packages']:
        for candidate in results.get(model_key, []):
            for issue in candidate.issues:
                finding_code = issue.code
                if not finding_code:
                    # Fallback — should never happen after engine update
                    finding_code = 'UNKNOWN'

                # Look up resolution from rules (NO message-string matching)
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

    # ── ROOT CAUSE DETECTION (structured, not message-based) ──
    # Build a map of entity+legacy_id → candidate for structured lookup
    candidate_map = {}
    for model_key in ['categories', 'suppliers', 'products', 'batches', 'packages']:
        for c in results.get(model_key, []):
            candidate_map[(c.target_model, c.legacy_id)] = c

    # For each finding, determine if it's a root cause or dependent
    # A finding is a ROOT_CAUSE if:
    #   1. Its finding_code has entries in DEPENDENCY_GRAPH with non-empty 'resolves'
    #   2. No other finding with a resolving code points to this finding's entity
    _classify_root_and_dependents(findings, candidate_map)

    # ── Summary ──
    root_causes = [f for f in findings if f.root_cause_code is None and f.depends_on_codes]
    root_causes_dedup = []
    seen_rc = set()
    for f in findings:
        # A finding is a root cause if it resolves something AND is not itself dependent
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

    # Build dependency chains
    dependency_chains = _build_dependency_chains(findings, candidate_map)

    return {
        'findings': findings,
        'summary': summary,
        'root_causes': root_causes_dedup,
        'dependency_chains': dependency_chains,
    }


def _classify_root_and_dependents(findings, candidate_map):
    """
    For each finding, determine root cause and dependency relationships.

    Uses structured entity references, NOT message text.

    Algorithm:
    1. Find all Product findings with category issues (PRODUCT_CATEGORY_MISSING, PRODUCT_CATEGORY_INVALID)
    2. These are root causes for their entity+legacy_id
    3. Downstream Batch findings with BATCH_INVALID_PRODUCT on the same product → dependent
    4. Downstream Package findings with PACKAGE_ORPHAN_PRODUCT on the same product → dependent
    """
    # Step 1: Identify root causes from Product findings
    # Product findings are keyed by meat_parts.id (the product entity's legacy_id)
    product_root_causes = {}  # (entity, legacy_id) → finding_code of root cause
    product_root_causes_by_mp = {}  # meat_parts_id → finding_code (for quick lookup)
    for f in findings:
        if f.entity == 'Product' and f.finding_code in (
            FindingCode.PRODUCT_CATEGORY_MISSING,
            FindingCode.PRODUCT_CATEGORY_INVALID,
        ):
            key = (f.entity, f.legacy_id)
            product_root_causes[key] = f.finding_code
            product_root_causes_by_mp[str(f.legacy_id)] = f.finding_code
            product_root_causes_by_mp[int(f.legacy_id)] = f.finding_code

    # No pi→mp mapping needed here — the engine stores meat_parts_id on Package candidates directly

    # Step 2: Mark dependents based on candidate relationships
    # For Batch findings, check if the batch references a product with a root cause
    for f in findings:
        if f.entity == 'Batch' and f.finding_code == FindingCode.BATCH_INVALID_PRODUCT:
            # Find the candidate to get structured data
            c = candidate_map.get((f.entity, f.legacy_id))
            if c:
                # The batch's product_legacy_id tells us which product it references
                # SQLite returns strings, but root causes are keyed by int — try both
                product_legacy_id = c.data.get('product_legacy_id')
                if product_legacy_id is not None:
                    product_key = ('Product', product_legacy_id)
                    product_key_int = ('Product', int(product_legacy_id)) if str(product_legacy_id).isdigit() else product_key
                    root_code = product_root_causes.get(product_key) or product_root_causes.get(product_key_int)
                    if root_code:
                        f.root_cause_code = root_code
                        f.root_cause_entity = 'Product'
                        f.root_cause_legacy_id = product_legacy_id
                        f.depends_on_codes = [root_code]

        elif f.entity == 'Package' and f.finding_code == FindingCode.PACKAGE_ORPHAN_PRODUCT:
            c = candidate_map.get((f.entity, f.legacy_id))
            if c:
                # Package.product_legacy_id = product_info.id, but root cause is by meat_parts.id
                # Use meat_parts_id field (set by engine) to look up root cause
                meat_parts_id = c.data.get('meat_parts_id')
                product_legacy_id = c.data.get('product_legacy_id')
                lookup_id = meat_parts_id if meat_parts_id is not None else product_legacy_id
                if lookup_id is not None:
                    product_key = ('Product', lookup_id)
                    product_key_int = ('Product', int(lookup_id)) if str(lookup_id).isdigit() else product_key
                    root_code = product_root_causes.get(product_key) or product_root_causes.get(product_key_int)
                    if root_code:
                        f.root_cause_code = root_code
                        f.root_cause_entity = 'Product'
                        f.root_cause_legacy_id = lookup_id
                        f.depends_on_codes = [root_code]


def _build_dependency_chains(findings, candidate_map):
    """Build structured dependency chains for reporting."""
    chains = []
    # Group dependents by root cause
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
# PROVISIONAL MAPPINGS
# ============================================================

# Evidence-based mappings that are NOT yet approved for auto-apply.
# Each requires explicit business confirmation.
PROVISIONAL_MAPPINGS = {
    'pending_to_packed': {
        'description': 'Map storage_status "pending" → PACKED state',
        'confidence': 'HIGH',
        'status': 'PROVISIONAL',
        'requires_business_confirmation': True,
        'evidence': [
            'freeze_started=None (never frozen)',
            'thaw_started=None (never thawed)',
            'thaw_queue_position=0 (never in queue)',
            'freeze_duration_minutes=0 (default/template)',
            'thaw_duration_hours=24 (default/template)',
            'activated=1 (in use)',
            'mfg dates are recent',
        ],
        'affected_finding_code': FindingCode.PACKAGE_UNKNOWN_STORAGE_STATUS,
    },
    'product_10_to_chicken': {
        'description': 'Assign Category CHICKEN to Product #10 (ปีกกลาง)',
        'confidence': 'HIGH',
        'status': 'PROVISIONAL',
        'requires_business_confirmation': True,
        'evidence': [
            'product name = "ปีกกลาง" (chicken mid-wing)',
            'barcode_prefix = 1002',
            'prefix pattern: 10xx = chicken products (consistent with all other chicken products)',
            'No other product category fits chicken mid-wing',
        ],
        'expected_impact': {
            'product_becomes_valid': 1,
            'batch_becomes_valid': 1,
            'packages_become_valid': 27,
            'total_migratable': 29,
        },
        'affected_finding_code': FindingCode.PRODUCT_CATEGORY_MISSING,
    },
    'assign_new_skus': {
        'description': 'Assign new SKUs for duplicate pairs instead of merging',
        'confidence': 'HIGH',
        'status': 'PROVISIONAL',
        'requires_business_confirmation': True,
        'evidence': [
            'MP-1108: meat_parts #3 (เศษไก่ติดหนัง) vs #6 (เศษไก่ BL3) — different names, different kcal',
            'MP-8206: meat_parts #7 (สะโพกหมูสไลด์) vs #22 (สะโพกหมู) — different names, sliced vs whole',
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

    # ── Root causes with dependency chains ──
    if root_causes:
        print('-' * 60)
        print('  ROOT CAUSES AND DEPENDENCY CHAINS')
        print('-' * 60)
        for rc in root_causes:
            print(f'  🔴 {rc.finding_code}')
            print(f'     Entity: {rc.entity} #{rc.legacy_id}')
            print(f'     Evidence: {rc.evidence}')
            # Find dependents
            dependents = [f for f in findings if rc.entity in (f.root_cause_entity or '') and rc.legacy_id == f.root_cause_legacy_id]
            if dependents:
                print(f'     ↓ Dependent findings ({len(dependents)}):')
                for d in dependents:
                    print(f'       → {d.finding_code} ({d.entity} #{d.legacy_id})')
            print()

    # ── Group by resolution ──
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
            entity_id = f.legacy_id
            label = f'#{entity_id}'
            root_tag = f' [ROOT: {f.root_cause_code}]' if f.root_cause_code and not f.depends_on_codes else ''
            dep_tag = f' [DEPENDS ON: {",".join(f.depends_on_codes)}]' if f.depends_on_codes else ''
            print(f'    {f.entity:12s} {label}{root_tag}{dep_tag}')
            print(f'      Code: {f.finding_code}')
            print(f'      Rule: {f.rule}')
            if f.evidence:
                print(f'      Evidence: {f.evidence}')
            print()

    # ── Provisional mappings ──
    print('-' * 60)
    print('  PROVISIONAL MAPPINGS (require business confirmation)')
    print('-' * 60)
    for key, pm in PROVISIONAL_MAPPINGS.items():
        print(f'  📋 {key}')
        print(f'     Description: {pm["description"]}')
        print(f'     Confidence: {pm["confidence"]}')
        print(f'     Status: {pm["status"]}')
        print(f'     Requires confirmation: {pm["requires_business_confirmation"]}')
        print()

    print('=' * 60)
    print('RESOLUTION COMPLETE — NO DATA WAS MODIFIED')
    print('=' * 60)
    print()
