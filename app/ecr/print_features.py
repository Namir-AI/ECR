"""Current browser-print mode; server PDF retained for future owner review."""

# Enable only after separate owner approval to restore the server PDF workflow.
SERVER_PDF_ENABLED = False


def server_pdf_enabled() -> bool:
    return SERVER_PDF_ENABLED
