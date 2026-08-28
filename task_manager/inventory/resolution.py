"""
Data Quality Resolution Engine — hardened classification with finding codes.

Every finding gets:
- A stable finding_code (never changes)
- A resolution category
- Root cause / dependency information
- Evidence from legacy data

This engine is READ-ONLY. It never modifies source data.
"""
from dataclasses import dataclass, field
from typing import Optional


# ============================================================
# FINDING CODES
# ============================================================

class FindingCode:
    # Category
    CATEGORY_EMPTY = 'CATEGORY_EMPTY'
    CATEGORY_TEST_DATA = 'CATEGORY_TEST_DATA'

    # Supplier
    SUPPLIER_EMPTY = 'SUPPLIER_EMPTY'
    SUPPLIER_DUPLICATE = 'SUPPLIER_DUPLICATE'

    # Product
    PRODUCT_CATEGORY_MISSING = 'PRODUCT_CATEGORY_MISSING'
    PRODUCT_CATEGORY_INVALID = 'PRODUCT_CATEGORY_INVALID'
    PRODUCT_DUPLICATE_SKU = 'PRODUCT_DUPLICATE_SKU'
    PRODUCT_NAME_EMPTY = 'PRODUCT_NAME_EMPTY'

    # Batch
    BATCH_INVALID_PRODUCT = 'BATCH_INVALID_PRODUCT'
    BATCH_WEIGHT_ZERO = 'BATCH_WEIGHT_ZERO'
    BATCH_MISSING_SUPPLIER = 'BATCH_MISSING_SUPPLIER'
    BATCH_INVALID_LOT = 'BATCH_INVALID_LOT'

    # Package
    PACKAGE_ORPHAN_PRODUCT = 'PACKAGE_ORPHAN_PRODUCT'
    PACKAGE_UNKNOWN_STORAGE_STATUS = 'PACKAGE_UNKNOWN_STORAGE_STATUS'
    PACKAGE_STATE_CONFLICT = 'PACKAGE_STATE_CONFLICT'
    PACKAGE_DUPLICATE_BARCODE = 'PACKAGE_DUPLICATE_BARCODE'
    PACKAGE_EMPTY_BARCODE = 'PACKAGE_EMPTY_BARCODE'
    PACKAGE_INVALID_WEIGHT = 'PACKAGE_INVALID_WEIGHT'
    PACKAGE_NEGATIVE_PRICE = 'PACKAGE_NEGATIVE_PRICE'
    PACKAGE_DUPLICATE_LOYVERSE_SKU = 'PACKAGE_DUPLICATE_LOYVERSE_SKU'


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
    root_cause: Optional[str] = None  # finding_code of root cause
    depends_on: list = field(default_factory=list)  # list of finding_codes
    affected_records: int = 1
    evidence: str = ''


# ============================================================
# ROOT CAUSE DEPENDENCY GRAPH
# ============================================================

# When a root cause is fixed, these dependent findings are resolved
DEPENDENCY_GRAPH = {
    FindingCode.PRODUCT_CATEGORY_MISSING: [
        FindingCode.BATCH_INVALID_PRODUCT,
        FindingCode.PACKAGE_ORPHAN_PRODUCT,
    ],
    FindingCode.PRODUCT_CATEGORY_INVALID: [
        FindingCode.BATCH_INVALID_PRODUCT,
        FindingCode.PACKAGE_ORPHAN_PRODUCT,
    ],
    FindingCode.PACKAGE_ORPHAN_PRODUCT: [],  # leaf — no downstream
    FindingCode.BATCH_INVALID_PRODUCT: [],   # leaf — no downstream
}


# ============================================================
# CLASSIFICATION ENGINE
# ============================================================

def classify_findings(results):
    """
    Classify dry-run findings with stable codes, root causes, and dependencies.

    Returns:
        dict: {findings: [Finding], summary: dict, root_causes: list}
    """
    findings = []

    # ── Category ──
    for c in results.get('categories', []):
        for issue in c.issues:
            if 'test' in issue.message.lower():
                findings.append(Finding(
                    finding_code=FindingCode.CATEGORY_TEST_DATA,
                    entity='Category', legacy_id=c.legacy_id,
                    resolution=Resolution.ACCEPTED_EXCEPTION,
                    rule='SKIP — test data, do not migrate',
                    message=issue.message, severity=issue.severity,
                    evidence=f'Category name="{c.data.get("name", "")}" is test data',
                ))
            elif 'empty' in issue.message.lower():
                findings.append(Finding(
                    finding_code=FindingCode.CATEGORY_EMPTY,
                    entity='Category', legacy_id=c.legacy_id,
                    resolution=Resolution.MIGRATION_BLOCKER,
                    rule='Cannot create Category without name',
                    message=issue.message, severity=issue.severity,
                ))
            elif 'duplicate' in issue.message.lower():
                findings.append(Finding(
                    finding_code=FindingCode.CATEGORY_EMPTY,  # reuse for now
                    entity='Category', legacy_id=c.legacy_id,
                    resolution=Resolution.MANUAL_REVIEW,
                    rule='Duplicate category code — need human decision',
                    message=issue.message, severity=issue.severity,
                ))

    # ── Supplier ──
    for c in results.get('suppliers', []):
        for issue in c.issues:
            if 'empty' in issue.message.lower():
                findings.append(Finding(
                    finding_code=FindingCode.SUPPLIER_EMPTY,
                    entity='Supplier', legacy_id=c.legacy_id,
                    resolution=Resolution.MIGRATION_BLOCKER,
                    rule='Cannot create Supplier without name',
                    message=issue.message, severity=issue.severity,
                ))
            elif 'duplicate' in issue.message.lower():
                findings.append(Finding(
                    finding_code=FindingCode.SUPPLIER_DUPLICATE,
                    entity='Supplier', legacy_id=c.legacy_id,
                    resolution=Resolution.MANUAL_REVIEW,
                    rule='Duplicate supplier name — need human decision',
                    message=issue.message, severity=issue.severity,
                ))

    # ── Product ──
    for c in results.get('products', []):
        for issue in c.issues:
            if 'category reference missing' in issue.message.lower():
                findings.append(Finding(
                    finding_code=FindingCode.PRODUCT_CATEGORY_MISSING,
                    entity='Product', legacy_id=c.legacy_id,
                    resolution=Resolution.MIGRATION_BLOCKER,
                    rule='Cannot create Product without Category',
                    message=issue.message, severity=issue.severity,
                    root_cause=FindingCode.PRODUCT_CATEGORY_MISSING,
                    evidence=f'Product "{c.data.get("name", "")}" has no category_id',
                ))
            elif 'category reference invalid' in issue.message.lower():
                findings.append(Finding(
                    finding_code=FindingCode.PRODUCT_CATEGORY_INVALID,
                    entity='Product', legacy_id=c.legacy_id,
                    resolution=Resolution.MIGRATION_BLOCKER,
                    rule='Category reference points to test data — skip product',
                    message=issue.message, severity=issue.severity,
                    root_cause=FindingCode.PRODUCT_CATEGORY_INVALID,
                    evidence=f'Product "{c.data.get("name", "")}" references category="test"',
                ))
            elif 'duplicate sku' in issue.message.lower():
                findings.append(Finding(
                    finding_code=FindingCode.PRODUCT_DUPLICATE_SKU,
                    entity='Product', legacy_id=c.legacy_id,
                    resolution=Resolution.MANUAL_REVIEW,
                    rule='Duplicate SKU — need decision: merge or assign new SKU',
                    message=issue.message, severity=issue.severity,
                    evidence=c.data.get('sku', ''),
                ))

    # ── Batch ──
    for c in results.get('batches', []):
        for issue in c.issues:
            if 'product reference' in issue.message.lower():
                # Determine root cause
                root = None
                if 'meat_parts #10' in issue.message:
                    root = FindingCode.PRODUCT_CATEGORY_MISSING
                findings.append(Finding(
                    finding_code=FindingCode.BATCH_INVALID_PRODUCT,
                    entity='Batch', legacy_id=c.legacy_id,
                    resolution=Resolution.MIGRATION_BLOCKER,
                    rule='Batch references invalid product — cannot create',
                    message=issue.message, severity=issue.severity,
                    root_cause=root,
                    depends_on=[root] if root else [],
                ))
            elif 'supplier' in issue.message.lower() and 'warning' in issue.severity.lower():
                findings.append(Finding(
                    finding_code=FindingCode.BATCH_MISSING_SUPPLIER,
                    entity='Batch', legacy_id=c.legacy_id,
                    resolution=Resolution.AUTO_FIX_SAFE,
                    rule='Missing supplier — create Batch with placeholder supplier',
                    message=issue.message, severity=issue.severity,
                ))
            elif 'weight' in issue.message.lower() and 'info' in issue.severity.lower():
                findings.append(Finding(
                    finding_code=FindingCode.BATCH_WEIGHT_ZERO,
                    entity='Batch', legacy_id=c.legacy_id,
                    resolution=Resolution.ACCEPTED_EXCEPTION,
                    rule='Product_info.weight=0.0 — informational only, Package.weight from Product_list',
                    message=issue.message, severity=issue.severity,
                    affected_records=1,
                    evidence='Product_info.weight is lot total weight (not recorded). Package physical weight comes from Product_list.weight.',
                ))
            elif 'invalid lot' in issue.message.lower():
                findings.append(Finding(
                    finding_code=FindingCode.BATCH_INVALID_LOT,
                    entity='Batch', legacy_id=c.legacy_id,
                    resolution=Resolution.MANUAL_REVIEW,
                    rule='Invalid lot number — needs review',
                    message=issue.message, severity=issue.severity,
                ))

    # ── Package ──
    for c in results.get('packages', []):
        for issue in c.issues:
            if 'product reference' in issue.message.lower():
                # Determine root cause from message
                root = None
                if 'meat_parts #10' in issue.message:
                    root = FindingCode.PRODUCT_CATEGORY_MISSING
                findings.append(Finding(
                    finding_code=FindingCode.PACKAGE_ORPHAN_PRODUCT,
                    entity='Package', legacy_id=c.legacy_id,
                    resolution=Resolution.MIGRATION_BLOCKER,
                    rule='Package references invalid product chain',
                    message=issue.message, severity=issue.severity,
                    root_cause=root,
                    depends_on=[root] if root else [],
                ))
            elif 'unknown storage_status' in issue.message.lower():
                findings.append(Finding(
                    finding_code=FindingCode.PACKAGE_UNKNOWN_STORAGE_STATUS,
                    entity='Package', legacy_id=c.legacy_id,
                    resolution=Resolution.MIGRATION_BLOCKER,
                    rule='Unmapped storage_status — need decision',
                    message=issue.message, severity=issue.severity,
                    evidence='No freeze/thaw activity, thaw_queue=0, activated=1, recent mfg date → likely PACKED',
                ))
            elif 'empty barcode' in issue.message.lower():
                findings.append(Finding(
                    finding_code=FindingCode.PACKAGE_EMPTY_BARCODE,
                    entity='Package', legacy_id=c.legacy_id,
                    resolution=Resolution.MIGRATION_BLOCKER,
                    rule='Package must have a barcode',
                    message=issue.message, severity=issue.severity,
                ))
            elif 'duplicate barcode' in issue.message.lower():
                findings.append(Finding(
                    finding_code=FindingCode.PACKAGE_DUPLICATE_BARCODE,
                    entity='Package', legacy_id=c.legacy_id,
                    resolution=Resolution.MIGRATION_BLOCKER,
                    rule='Duplicate barcode — physical package identity conflict',
                    message=issue.message, severity=issue.severity,
                ))
            elif 'invalid weight' in issue.message.lower():
                findings.append(Finding(
                    finding_code=FindingCode.PACKAGE_INVALID_WEIGHT,
                    entity='Package', legacy_id=c.legacy_id,
                    resolution=Resolution.MIGRATION_BLOCKER,
                    rule='Zero or negative weight — cannot create Package',
                    message=issue.message, severity=issue.severity,
                ))
            elif 'inconsistent' in issue.message.lower() or 'conflicting' in issue.message.lower():
                findings.append(Finding(
                    finding_code=FindingCode.PACKAGE_STATE_CONFLICT,
                    entity='Package', legacy_id=c.legacy_id,
                    resolution=Resolution.MANUAL_REVIEW,
                    rule='Conflicting state fields — need human decision',
                    message=issue.message, severity=issue.severity,
                ))
            elif 'negative selling_price' in issue.message.lower():
                findings.append(Finding(
                    finding_code=FindingCode.PACKAGE_NEGATIVE_PRICE,
                    entity='Package', legacy_id=c.legacy_id,
                    resolution=Resolution.MANUAL_REVIEW,
                    rule='Negative price — need review',
                    message=issue.message, severity=issue.severity,
                ))
            elif 'duplicate loyverse' in issue.message.lower():
                findings.append(Finding(
                    finding_code=FindingCode.PACKAGE_DUPLICATE_LOYVERSE_SKU,
                    entity='Package', legacy_id=c.legacy_id,
                    resolution=Resolution.MANUAL_REVIEW,
                    rule='Duplicate Loyverse SKU — need review',
                    message=issue.message, severity=issue.severity,
                ))

    # ── Identify root causes and dependent findings ──
    root_causes = _identify_root_causes(findings)
    _mark_dependents(findings, root_causes)

    # ── Summary ──
    summary = {
        'total_findings': len(findings),
        'auto_fix_safe': sum(1 for f in findings if f.resolution == Resolution.AUTO_FIX_SAFE),
        'manual_review': sum(1 for f in findings if f.resolution == Resolution.MANUAL_REVIEW),
        'structural_problem': sum(1 for f in findings if f.resolution == Resolution.STRUCTURAL_PROBLEM),
        'migration_blocker': sum(1 for f in findings if f.resolution == Resolution.MIGRATION_BLOCKER),
        'accepted_exception': sum(1 for f in findings if f.resolution == Resolution.ACCEPTED_EXCEPTION),
        'root_causes': len(root_causes),
        'dependent_findings': sum(1 for f in findings if f.depends_on),
    }

    return {
        'findings': findings,
        'summary': summary,
        'root_causes': root_causes,
    }


def _identify_root_causes(findings):
    """Find findings that are root causes (not dependent on anything)."""
    root_causes = []
    for f in findings:
        if f.root_cause and f.finding_code == f.root_cause:
            # This finding IS the root cause
            if not any(rc.finding_code == f.finding_code for rc in root_causes):
                root_causes.append(f)
    return root_causes


def _mark_dependents(findings, root_causes):
    """Mark findings that are downstream of root causes."""
    for f in findings:
        if f.depends_on:
            for rc in root_causes:
                if rc.finding_code in f.depends_on:
                    f.root_cause = rc.finding_code


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
            dependents = [f for f in findings if rc.finding_code in f.depends_on]
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
            root_tag = f' [ROOT: {f.root_cause}]' if f.root_cause and f.finding_code == f.root_cause else ''
            dep_tag = f' [DEPENDS ON: {",".join(f.depends_on)}]' if f.depends_on else ''
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
