"""Exercise color fidelity across the native etcpak wheel variants."""
from types import SimpleNamespace

import pytest
from PIL import Image
from AssetbundleUtils.UnityPy_AOV.enums import BuildTarget, TextureFormat as TF
from AssetbundleUtils.UnityPy_AOV.export.Texture2DConverter import (
    get_image_from_texture2d, image_to_texture2d,
)


@pytest.mark.parametrize("codec,alpha", [
    (TF.ETC_RGB4, 255), (TF.ETC2_RGB, 255), (TF.ETC2_RGBA8, 180),
])
def test_etc_round_trip_preserves_red_blue_and_alpha(codec, alpha):
    color = (46, 132, 210, alpha)
    # NPOT case exercises the same edge padding used by small mips.
    image = Image.new("RGBA", (9, 7), color)
    encoded, fmt = image_to_texture2d(image, codec)
    assert fmt == codec
    assert len(encoded) == 6 * (16 if codec == TF.ETC2_RGBA8 else 8)
    tex = SimpleNamespace(
        image_data=encoded, m_TextureFormat=fmt, m_Width=9, m_Height=7,
        version=(2022, 3, 5), platform=BuildTarget.Android,
    )
    pixel = get_image_from_texture2d(tex).getpixel((2, 2))
    assert max(abs(a - b) for a, b in zip(color, pixel)) <= 15, (codec, pixel)
