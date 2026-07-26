from website.models import GeneralInformation, Paragraph, Picture


def seed_site_context_fixtures():
    """
    src.context_processors.data_processor runs on every template render and
    hard-requires specific GeneralInformation/Picture/Paragraph rows to exist
    (pre-existing site behavior, unrelated to the wallet app). The real dev/
    prod database already has this seed data; a fresh test database does not,
    so any test that renders a template needs it created first.
    """
    GeneralInformation.objects.create(
        phone_number='000', email='school@example.com', preamble='x', address='x',
        # website/base.html and header.html unconditionally do
        # {{data.logo.url}}/{{data.footer_logo.url}} - an unset ImageField
        # raises ValueError on .url access (not just a missing file, since
        # Django only checks the field has a name assigned, never that the
        # file exists on disk).
        logo='pics/placeholder.png',
        footer_logo='pics/placeholder.png',
    )
    for title in ['about1', 'choose1', 'car5', 'result-checker', 'grading-system', 'about header bg']:
        Picture.objects.create(title=title, image='pics/placeholder.png')
    Paragraph.objects.create(title='choose_p', content='x')
