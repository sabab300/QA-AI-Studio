"""Compatibility aliases for logical source types, independent of UI mode."""
def canonical_source_type(value):
    original=str(value or 'Files').strip()
    return {'FILE':'Files','FILES':'Files','FOLDER':'Files','MANUAL_UPLOAD':'Files',
        'SMART_UPLOAD':'Files','API_COLLECTION':'API Collection','URL_CAPTURE':'URL'}.get(original.upper(),original)
