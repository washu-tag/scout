import zipfile

import xnat_frame_location_jar as jar


def test_the_jar_is_the_same_for_every_site_and_build(tmp_path):
    first, second = tmp_path / "a.jar", tmp_path / "b.jar"
    jar.build(first)
    jar.build(second)
    assert first.read_bytes() == second.read_bytes()

    with zipfile.ZipFile(first) as built:
        assert built.namelist()[-1] == jar.ENTRY
        assert "META-INF/resources/templates/screens/header/" in built.namelist()
        page = built.read(jar.ENTRY).decode()
    assert "${server_hostname}" not in page
    assert f"var launchpad = {jar.LAUNCHPAD};" in page
    assert page.startswith("#if($data.request.method == 'GET')\n<script>\n")
