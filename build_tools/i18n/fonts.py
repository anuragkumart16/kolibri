# -*- coding: utf-8 -*-
"""
For usage instructions, see:
    https://kolibri-dev.readthedocs.io/en/develop/references/i18n.html
"""
import argparse
import base64
import io
import json
import logging
import os
import re
import sys
import tempfile

import noto_source
import utils
from fontTools import merge
from fontTools import subset

logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)
logging.getLogger("fontTools").setLevel(logging.WARNING)
logging.StreamHandler(sys.stdout)


"""
Constants
"""

OUTPUT_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        os.pardir,
        os.pardir,
        "kolibri",
        "core",
        "static",
        "assets",
        "fonts",
    )
)


FONT_TOOLS_OPTIONS = subset.Options()
FONT_TOOLS_OPTIONS.flavor = "woff"  # most widely supported format
FONT_TOOLS_OPTIONS.ignore_missing_unicodes = True  # important for subsetting

# basic latin glyphs
NOTO_SANS_LATIN = "NotoSans"

# font family name conventions
SCOPE_FULL = "noto-full"
SCOPE_SUBSET = "noto-subset"
SCOPE_COMMON = "noto-common"

"""
Shared helpers
"""


_FONT_FACE = """
@font-face {{
  font-family: '{family}';
  src: url('{url}') format('woff');
  font-style: normal;
  font-weight: {weight};
  unicode-range: {unicodes};
  font-display: swap;
}}
"""


def _gen_font_face(family, url, is_bold, unicodes):
    weight = "bold" if is_bold else "normal"
    return _FONT_FACE.format(family=family, url=url, weight=weight, unicodes=unicodes)


def _scoped(scope, name):
    return "{}.{}".format(scope, name)


@utils.memoize
def _woff_font_path(name, is_bold):
    file_name = "{name}.{weight}.woff".format(
        name=name, weight="700" if is_bold else "400"
    )
    return os.path.join(OUTPUT_PATH, file_name)


def _load_font(path):
    return subset.load_font(path, FONT_TOOLS_OPTIONS, dontLoadGlyphNames=True)


@utils.memoize
def _font_priorities(default_font):
    """
    Given a default font, return a list of all possible font names roughly in the order
    that we ought to look for glyphs in. Many fonts contain overlapping sets of glyphs.

    Without doing this: we risk loading a bunch of random font files just because they
    happen to contain one of the glyphs, and we also risk loading the 'wrong' version
    of the glyphs if they happen to differ.
    """

    # start with the default
    font_names = [default_font]

    # look in the latin set next
    if default_font is not NOTO_SANS_LATIN:
        font_names.append(NOTO_SANS_LATIN)

    # then look at the rest of the supported languages' default fonts
    for lang_info in utils.supported_languages():
        name = lang_info[utils.KEY_DEFAULT_FONT]
        if name not in font_names:
            font_names.append(name)

    # finally look at the remaining langauges
    font_names.extend([fn for fn in noto_source.FONT_MANIFEST if fn not in font_names])
    return font_names


@utils.memoize
def _font_glyphs(font_path):
    """
    extract set of all glyphs from a font
    """
    glyphs = set()
    for table in _load_font(font_path)["cmap"].tables:
        glyphs |= set(table.cmap.keys())
    return glyphs


def _clean_up(scope):
    """
    Delete all files in OUTPUT_PATH that match the scope
    """
    css_pattern = r"{}.*?\.css".format(scope)
    woff_pattern = r"{}.*?\.woff".format(scope)
    for name in os.listdir(OUTPUT_PATH):
        if re.match(css_pattern, name) or re.match(woff_pattern, name):
            os.unlink(os.path.join(OUTPUT_PATH, name))


"""
CSS helpers
"""


CSS_HEADER = """
/*
 * This is an auto-generated file, so any manual edits will be overridden.
 *
 * To regenerate, see instructions here:
 *   https://kolibri-dev.readthedocs.io/en/develop/references/i18n.html
 *
 * This file was generated by build_tools/i18n/fonts.py
 */
"""


def _list_to_ranges(input_list):
    """
    Iterator of ranges of contiguous numbers from a list of integers.
    Ranges returned are [x, y) – in other words, y is non-inclusive.
    (from: http://code.activestate.com/recipes/496682/)
    """
    new_list = list(input_list)
    new_list.sort()
    start = new_list[0]
    currentrange = [start, start + 1]
    for item in new_list[1:]:
        if currentrange[1] == item:
            currentrange[1] += 1  # contiguous
        else:
            yield tuple(currentrange)  # new range start
            currentrange = [item, item + 1]
    yield tuple(currentrange)  # last range


def _fmt_code(code):
    return "{:x}".format(code).upper()


def _fmt_range(glyphs):
    """
    Generates a font-face-compatible 'unicode range' attribute for a given set of glyphs
    """
    fmt_ranges = []
    for r in _list_to_ranges(sorted(glyphs)):
        if r[0] == r[1] - 1:
            fmt_ranges.append("U+{}".format(_fmt_code(r[0])))
        else:
            fmt_ranges.append("U+{}-{}".format(_fmt_code(r[0]), _fmt_code(r[1] - 1)))
    return ",".join(fmt_ranges)


"""
Full Fonts
"""


def _full_font_face(font_family, font_name, is_bold, omit_glyphs=set()):
    """
    generate the CSS reference for a single full font
    """
    file_path = _woff_font_path(_scoped(SCOPE_FULL, font_name), is_bold=is_bold)
    file_name = os.path.basename(file_path)
    glyphs = _font_glyphs(file_path) - omit_glyphs
    if not glyphs:
        return ""
    return _gen_font_face(
        font_family, file_name, is_bold=is_bold, unicodes=_fmt_range(glyphs)
    )


def _gen_full_css_modern(lang_info):
    """
    Generates listing for all full fonts, segmented by unicode ranges and weights
    """

    # skip previously accounted for glyphs so there is no overlap between font-faces
    previous_glyphs = set()

    # all available fonts
    font_faces = []
    for font_name in _font_priorities(lang_info[utils.KEY_DEFAULT_FONT]):
        font_faces.append(
            _full_font_face(
                SCOPE_FULL, font_name, is_bold=False, omit_glyphs=previous_glyphs
            )
        )
        font_faces.append(
            _full_font_face(
                SCOPE_FULL, font_name, is_bold=True, omit_glyphs=previous_glyphs
            )
        )

        # Assumes all four variants have the same glyphs, from the content Regular font
        previous_glyphs |= _font_glyphs(
            _woff_font_path(_scoped(SCOPE_FULL, font_name), is_bold=False)
        )

    output_name = os.path.join(
        OUTPUT_PATH,
        "{}.modern.css".format(_scoped(SCOPE_FULL, lang_info[utils.KEY_INTL_CODE])),
    )
    logging.info("Writing {}".format(output_name))
    with open(output_name, "w") as f:
        f.write(CSS_HEADER)
        f.write("".join(font_faces))


def _gen_full_css_basic(lang_info):
    output_name = os.path.join(
        OUTPUT_PATH,
        "{}.basic.css".format(_scoped(SCOPE_FULL, lang_info[utils.KEY_INTL_CODE])),
    )
    logging.info("Writing {}".format(output_name))
    with open(output_name, "w") as f:
        f.write(CSS_HEADER)
        default_font = lang_info[utils.KEY_DEFAULT_FONT]
        f.write(_full_font_face(SCOPE_FULL, default_font, is_bold=False))
        f.write(_full_font_face(SCOPE_FULL, default_font, is_bold=True))


def _write_full_font(font_name, is_bold):
    font = _load_font(noto_source.get_path(font_name, is_bold=is_bold))
    output_name = _woff_font_path(_scoped(SCOPE_FULL, font_name), is_bold=is_bold)
    logging.info("Writing {}".format(output_name))
    font.save(output_name)


def command_gen_full_fonts():
    logging.info("generating full fonts...")

    _clean_up(SCOPE_FULL)

    for font_name in noto_source.FONT_MANIFEST:
        _write_full_font(font_name, is_bold=False)
        _write_full_font(font_name, is_bold=True)

    languages = utils.supported_languages(include_in_context=True, include_english=True)
    for lang_info in languages:
        _gen_full_css_modern(lang_info)
        _gen_full_css_basic(lang_info)

    logging.info("finished generating full fonts")


"""
Subset fonts
"""


def _write_inline_font(file_object, font_path, font_family, is_bold):
    """
    Inlines a font as base64 encoding within a CSS file
    """
    with io.open(font_path, mode="rb") as f:
        data = f.read()
    data_uri = "data:application/x-font-woff;charset=utf-8;base64,{}".format(
        base64.b64encode(data).decode()
    )
    glyphs = _font_glyphs(font_path)
    if not glyphs:
        return
    file_object.write(
        _gen_font_face(
            family=font_family,
            url=data_uri,
            is_bold=is_bold,
            unicodes=_fmt_range(glyphs),
        )
    )


def _generate_inline_font_css(name, font_family):
    """
    Generate CSS and clean up inlined woff files
    """

    font_path_reg = _woff_font_path(name, is_bold=False)
    font_path_bold = _woff_font_path(name, is_bold=True)

    output_name = os.path.join(OUTPUT_PATH, "{}.css".format(name))
    logging.info("Writing {}".format(output_name))
    with open(output_name, "w") as f:
        f.write(CSS_HEADER)
        _write_inline_font(f, font_path_reg, font_family, is_bold=False)
        _write_inline_font(f, font_path_bold, font_family, is_bold=True)

    os.unlink(font_path_reg)
    os.unlink(font_path_bold)


def _get_subset_font(source_file_path, text):
    """
    Given a source file and some text, returns a new, in-memory fontTools Font object
    that has only the glyphs specified in the set.

    Note that passing actual text instead of a glyph set to the subsetter allows it to
    generate appropriate ligatures and other features important for correct rendering.
    """
    if not os.path.exists(source_file_path):
        logging.error("'{}' not found".format(source_file_path))

    font = _load_font(source_file_path)
    subsetter = subset.Subsetter(options=FONT_TOOLS_OPTIONS)
    subsetter.populate(text=text)
    subsetter.subset(font)
    return font


def _get_lang_strings(locale_dir):
    """
    Text used in a particular language
    """

    strings = []

    for file_name in os.listdir(locale_dir):
        if not file_name.endswith(".json"):
            continue

        file_path = os.path.join(locale_dir, file_name)
        with io.open(file_path, mode="r", encoding="utf-8") as f:
            lang_strings = json.load(f).values()

        for s in lang_strings:
            s = re.sub("\W", " ", s)  # clean whitespace
            strings.append(s)
            strings.append(s.upper())

    return strings


@utils.memoize
def _get_common_strings():
    """
    Text useful for all languages: displaying the language switcher, Kolibri version
    numbers, symbols, and other un-translated text
    """

    # Special characters that are used directly in untranslated template strings.
    # Search the codebase with this regex to find new ones: [^\x00-\x7F©–—…‘’“”•→›]
    strings = [
        chr(0x0),  # null
        "©",
        "–",  # en dash
        "—",  # em dash
        "…",
        "‘",
        "’",
        "“",
        "”",
        "•",
        "→",
        "›",
    ]

    # all the basic printable ascii characters
    strings.extend([chr(c) for c in range(32, 127)])

    # text from language names, both lower- and upper-case
    languages = utils.supported_languages(include_in_context=True, include_english=True)
    for lang in languages:
        strings.append(lang[utils.KEY_LANG_NAME])
        strings.append(lang[utils.KEY_LANG_NAME].upper())
        strings.append(lang[utils.KEY_ENG_NAME])
        strings.append(lang[utils.KEY_ENG_NAME].upper())

    return strings


def _merge_fonts(fonts, output_file_path):
    """
    Given a list of fontTools font objects, merge them and export to output_file_path.

    Implemenatation note: it would have been nice to pass the fonts directly to the
    merger, but the current fontTools implementation of Merger takes a list of file names
    """
    tmp = tempfile.gettempdir()
    f_names = []
    for i, f in enumerate(fonts):
        tmp_font_path = os.path.join(tmp, "{}.woff".format(i))
        f_names.append(tmp_font_path)
        f.save(tmp_font_path)
    merger = merge.Merger(options=FONT_TOOLS_OPTIONS)
    merged_font = merger.merge(f_names)
    merged_font.save(output_file_path)
    logging.info("created {}".format(output_file_path))


def _cannot_merge(font):
    # all fonts must have equal units per em for merging, and 1000 is most common
    return font["head"].unitsPerEm != 1000


def _subset_and_merge_fonts(text, default_font, subset_reg_path, subset_bold_path):
    """
    Given text, generate both a bold and a regular font that can render it.
    """
    reg_subsets = []
    bold_subsets = []
    skipped = []

    # track which glyphs are left
    remaining_glyphs = set([ord(c) for c in text])

    for font_name in _font_priorities(default_font):
        full_reg_path = _woff_font_path(_scoped(SCOPE_FULL, font_name), is_bold=False)
        full_bold_path = _woff_font_path(_scoped(SCOPE_FULL, font_name), is_bold=True)
        reg_subset = _get_subset_font(full_reg_path, text)
        bold_subset = _get_subset_font(full_bold_path, text)

        if _cannot_merge(reg_subset) or _cannot_merge(bold_subset):
            skipped.append(font_name)
            continue

        reg_subsets.append(reg_subset)
        bold_subsets.append(bold_subset)

        remaining_glyphs -= _font_glyphs(full_reg_path)
        if not remaining_glyphs:
            break

    _merge_fonts(reg_subsets, os.path.join(OUTPUT_PATH, subset_reg_path))
    _merge_fonts(bold_subsets, os.path.join(OUTPUT_PATH, subset_bold_path))


def command_gen_subset_fonts():
    """
    Creates custom fonts that attempt to contain all the glyphs and other font features
    that are used in user-facing text for the translation in each language.

    We make a separate subset font for common strings, which generally overaps somewhat
    with the individual language subsets. This slightly increases how much the client
    needs to download on first request, but reduces Kolibri's distribution size by a
    couple megabytes.
    """
    logging.info("generating subset fonts...")

    _clean_up(SCOPE_COMMON)
    _clean_up(SCOPE_SUBSET)

    _subset_and_merge_fonts(
        text=" ".join(_get_common_strings()),
        default_font=NOTO_SANS_LATIN,
        subset_reg_path=_woff_font_path(SCOPE_COMMON, is_bold=False),
        subset_bold_path=_woff_font_path(SCOPE_COMMON, is_bold=True),
    )

    languages = utils.supported_languages(include_in_context=True, include_english=True)
    for lang_info in languages:
        logging.info("gen subset for {}".format(lang_info[utils.KEY_ENG_NAME]))
        strings = []
        strings.extend(_get_lang_strings(utils.local_locale_path(lang_info)))
        strings.extend(_get_lang_strings(utils.local_perseus_locale_path(lang_info)))

        name = lang_info[utils.KEY_INTL_CODE]
        _subset_and_merge_fonts(
            text=" ".join(strings),
            default_font=lang_info[utils.KEY_DEFAULT_FONT],
            subset_reg_path=_woff_font_path(_scoped(SCOPE_SUBSET, name), is_bold=False),
            subset_bold_path=_woff_font_path(_scoped(SCOPE_SUBSET, name), is_bold=True),
        )

    # generate common subset file
    _generate_inline_font_css(name=SCOPE_COMMON, font_family=SCOPE_COMMON)

    # generate language-specific subset font files
    languages = utils.supported_languages(include_in_context=True, include_english=True)
    for lang in languages:
        _generate_inline_font_css(
            name=_scoped(SCOPE_SUBSET, lang[utils.KEY_INTL_CODE]),
            font_family=SCOPE_SUBSET,
        )

    logging.info("subsets created")


"""
Add source fonts
"""


def command_update_font_manifest(ref):
    noto_source.update_manifest(ref)


def command_download_source_fonts():
    noto_source.fetch_fonts()


"""
Main
"""


def main():
    """
    Generates files to support both 'basic' and a 'modern' browsers.

    Both browsers get the common and language-specific application subset fonts inline
    to load quickly and prevent a flash of unstyled text, at least for all application
    text. Full font files are linked and will load asynchronously.

    # Modern behavior

    Newer browsers have full support for the unicode-range attribute of font-face
    definitions, which allow the browser to download fonts as-needed based on the text
    observed. This allows us to make _all_ font alphabets available, and ensures that
    content will be rendered using the best font possible for all content, regardless
    of selected app language.

    # Basic behavior

    Older browsers do not fully support the unicode-range attribute, and will eagerly
    download all referenced fonts regardless of whether or not they are needed. This
    would have an unacceptable performance impact. As an alternative, we provide
    references to the full fonts for the user's currently-selected language, under the
    assumption that most of the content they use will be in that language.

    Content viewed in other languages using the basic variant should still usually
    display, albeit using system fonts.
    """

    description = "\n\nProcess fonts.\nSyntax: [command] [branch]\n\n"
    parser = argparse.ArgumentParser(description=description)
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser(
        "update-font-manifest",
        help="Update manifest from https://github.com/googlei18n/noto-fonts/",
    ).add_argument("ref", help="Github reference, e.g. commit or tag", type=str)

    subparsers.add_parser(
        "download-source-fonts",
        help="Download sources from https://github.com/googlei18n/noto-fonts/",
    )

    subparsers.add_parser(
        "generate-subset-fonts", help="Generate subset fonts based on app text"
    )

    subparsers.add_parser("generate-full-fonts", help="Generate full fonts")

    args = parser.parse_args()

    if args.command == "update-font-manifest":
        command_update_font_manifest(args.ref)
    elif args.command == "download-source-fonts":
        command_download_source_fonts()
    elif args.command == "generate-subset-fonts":
        command_gen_subset_fonts()
    elif args.command == "generate-full-fonts":
        command_gen_full_fonts()
    else:
        logging.warning("Unknown command\n")
        parser.print_help(sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
