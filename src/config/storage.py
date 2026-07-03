from django.contrib.staticfiles.storage import ManifestStaticFilesStorage


class CacheBustingStaticFilesStorage(ManifestStaticFilesStorage):
    """ManifestStaticFilesStorage without sourceMappingURL rewriting.

    Vendored bundles (leaflet.js, the *.min.js libs) keep their original
    sourceMappingURL comments but the .map files are not vendored, and the
    stock storage raises ValueError on any reference it cannot resolve.
    url()/@import hashing in CSS is kept as is.
    """

    patterns = tuple(
        (
            extension,
            tuple(p for p in pats if "sourceMappingURL" not in p[1]),
        )
        for extension, pats in ManifestStaticFilesStorage.patterns
    )
