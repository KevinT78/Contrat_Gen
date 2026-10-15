"""Allège les pièces stockées : photos de téléphone et PDF scannés, qu'elles
viennent du formulaire, d'un dépôt RH ou de l'import SharePoint.

Réglage : instance.json, compression = "couleur" (défaut) | "gris".
Photo -> JPEG, côté max réduit ; PDF -> seules ses images sont recompressées,
le texte n'est jamais touché ; un PDF signé électroniquement n'est pas touché
du tout (le réécrire casserait la signature). Au moindre doute (autre format, fichier abîmé,
module absent, résultat plus lourd), l'original est rendu tel quel.
Modules : pikepdf, Pillow, pillow-heif (requirements.txt).
"""
import io
import os

PHOTOS = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
# mode -> (mode Pillow, côté max en px, qualité JPEG)
REGLAGES = {"couleur": ("RGB", 2000, 75), "gris": ("L", 1600, 70)}
# Image d'un PDF plus petite : logo, signature, tampon. Rien à gagner, du
# risque à la repeindre.
IMAGE_MIN = 20 * 1024


def compressible(nom):
    ext = os.path.splitext(nom)[1].lower()
    return ext == ".pdf" or ext in PHOTOS


def compresser(nom, octets, mode="couleur"):
    """-> (nom, octets). Une photo devient un .jpg ; un PDF garde son nom."""
    stem, ext = os.path.splitext(nom)
    ext = ext.lower()
    try:
        if ext == ".pdf" and b"/ByteRange" in octets:    # signature électronique
            return nom, octets
        if ext == ".pdf":
            nouveau, nouveau_nom = _pdf(octets, *REGLAGES[mode]), nom
        elif ext in PHOTOS:
            nouveau, nouveau_nom = _photo(octets, *REGLAGES[mode]), stem + ".jpg"
        else:
            return nom, octets
    except Exception:                                   # noqa: BLE001 — l'original reste bon
        return nom, octets
    return (nouveau_nom, nouveau) if len(nouveau) < len(octets) else (nom, octets)


def _jpeg(im, couleurs, cote, qualite):
    from PIL import Image
    if im.mode in ("RGBA", "LA", "P", "PA"):            # transparent : fond blanc, pas noir
        im = im.convert("RGBA")
        fond = Image.new("RGBA", im.size, "white")
        im = Image.alpha_composite(fond, im)
    im = im.convert(couleurs)
    im.thumbnail((cote, cote))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=qualite, optimize=True)  # sans EXIF : ni GPS ni appareil
    return im, b.getvalue()


def _photo(octets, couleurs, cote, qualite):
    from PIL import Image, ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass                                # .heic : Image.open lèvera, original gardé
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(octets)))
    return _jpeg(im, couleurs, cote, qualite)[1]


def _pdf(octets, couleurs, cote, qualite):
    import pikepdf
    from pikepdf import PdfImage
    vues = set()
    with pikepdf.open(io.BytesIO(octets)) as doc:
        for page in doc.pages:
            for xo in page.get_images().values():
                brut = xo.read_raw_bytes()
                if xo.objgen in vues or xo.get("/ImageMask") or len(brut) < IMAGE_MIN:
                    continue
                vues.add(xo.objgen)
                try:
                    im = PdfImage(xo).as_pil_image()
                except Exception:           # noqa: BLE001 — JBIG2, CCITT : laissée telle quelle
                    continue
                im, jpeg = _jpeg(im, couleurs, cote, qualite)
                if len(jpeg) >= len(brut):
                    continue
                xo.write(jpeg, filter=pikepdf.Name.DCTDecode)
                xo.Width, xo.Height = im.size
                xo.ColorSpace = (pikepdf.Name.DeviceGray if couleurs == "L"
                                 else pikepdf.Name.DeviceRGB)
                xo.BitsPerComponent = 8
                for k in ("/DecodeParms", "/Decode", "/Mask"):
                    if k in xo:
                        del xo[k]
        out = io.BytesIO()
        doc.save(out, compress_streams=True,
                 object_stream_mode=pikepdf.ObjectStreamMode.generate)
        return out.getvalue()
