from testsupport import CONFIGMAP
from triage.yamldiff import MISSING, diff


def test_change_inside_embedded_helm_values():
    [c] = diff(CONFIGMAP, CONFIGMAP.replace("memory: 256Mi", "memory: 400Mi"))
    assert c.path == ("ConfigMap/demo-values", "data", "values.yaml", "resources", "limits", "memory")
    assert (c.old, c.new) == ("256Mi", "400Mi")


def test_removed_key_is_missing():
    [c] = diff(CONFIGMAP, CONFIGMAP.replace("        cpu: 500m\n", ""))
    assert c.path[-3:] == ("resources", "limits", "cpu") and c.new is MISSING


def test_list_items_diff_by_index():
    old = "kind: Deployment\nmetadata: {name: d}\nspec: {containers: [{name: a, image: x:1}]}\n"
    [c] = diff(old, old.replace("x:1", "x:2"))
    assert c.path == ("Deployment/d", "spec", "containers", "0", "image")


def test_list_length_change_is_one_whole_list_change():
    old = "kind: X\nmetadata: {name: n}\nitems: [1, 2]\n"
    [c] = diff(old, old.replace("[1, 2]", "[1, 2, 3]"))
    assert c.path == ("X/n", "items")


def test_multi_doc_files_are_keyed_by_kind_and_name():
    old = "kind: A\nmetadata: {name: one}\nv: 1\n---\nkind: A\nmetadata: {name: two}\nv: 1\n"
    [c] = diff(old, old.replace("name: two}\nv: 1", "name: two}\nv: 2"))
    assert c.path == ("A/two", "v")


def test_identical_is_empty():
    assert diff(CONFIGMAP, CONFIGMAP) == []
