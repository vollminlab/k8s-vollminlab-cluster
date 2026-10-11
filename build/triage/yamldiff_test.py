import pytest

from testsupport import CONFIGMAP
from triage.yamldiff import MISSING, DiffError, diff, expand


def test_change_inside_embedded_helm_values():
    [c] = diff(CONFIGMAP, CONFIGMAP.replace("memory: 256Mi", "memory: 400Mi"))
    assert c.path == (("v1", "ConfigMap", "demo", "demo-values"), "data", "values.yaml", "resources", "limits", "memory")
    assert (c.old, c.new) == ("256Mi", "400Mi")


def test_removed_key_is_missing():
    [c] = diff(CONFIGMAP, CONFIGMAP.replace("        cpu: 500m\n", ""))
    assert c.path[-3:] == ("resources", "limits", "cpu") and c.new is MISSING


def test_list_items_diff_by_index():
    old = "kind: Deployment\nmetadata: {name: d}\nspec: {containers: [{name: a, image: x:1}]}\n"
    [c] = diff(old, old.replace("x:1", "x:2"))
    assert c.path[1:] == ("spec", "containers", "0", "image")


def test_list_length_change_is_one_whole_list_change():
    old = "kind: X\nmetadata: {name: n}\nitems: [1, 2]\n"
    [c] = diff(old, old.replace("[1, 2]", "[1, 2, 3]"))
    assert c.path[1:] == ("items",)


def test_multi_doc_files_are_keyed_by_kind_and_name():
    old = "kind: A\nmetadata: {name: one}\nv: 1\n---\nkind: A\nmetadata: {name: two}\nv: 1\n"
    [c] = diff(old, old.replace("name: two}\nv: 1", "name: two}\nv: 2"))
    assert c.path[0][3] == "two" and c.path[1:] == ("v",)


def test_identical_is_empty():
    assert diff(CONFIGMAP, CONFIGMAP) == []


# ---- document identity ------------------------------------------------------------

HR = "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\nmetadata: {name: demo, namespace: demo}\nspec: {x: 1}\n"


def test_docs_are_keyed_by_api_version_kind_namespace_and_name():
    [c] = diff(HR, HR.replace("x: 1", "x: 2"))
    assert c.path[0] == ("helm.toolkit.fluxcd.io/v2", "HelmRelease", "demo", "demo")


def test_a_prepended_document_with_the_same_kind_and_name_is_an_error():
    hidden = HR.replace("namespace: demo", "namespace: kube-system").replace("x: 1", "evil: true")
    with pytest.raises(DiffError, match="adds or removes a document"):
        diff(HR, hidden + "---\n" + HR)


def test_an_appended_or_removed_document_is_an_error():
    other = HR.replace("name: demo,", "name: other,")
    with pytest.raises(DiffError, match="adds or removes"):
        diff(HR, HR + "---\n" + other)
    with pytest.raises(DiffError, match="adds or removes"):
        diff(HR + "---\n" + other, HR)


def test_an_added_empty_document_is_an_error():
    with pytest.raises(DiffError):
        diff(HR, HR + "---\n---\n")


def test_duplicate_document_keys_are_an_error():
    with pytest.raises(DiffError, match="twice"):
        diff(HR + "---\n" + HR, HR + "---\n" + HR.replace("x: 1", "x: 2"))


def test_moving_a_document_to_another_namespace_is_an_error():
    with pytest.raises(DiffError):
        diff(HR, HR.replace("namespace: demo", "namespace: kube-system"))


@pytest.mark.parametrize("bad", ["a:\n  b: 2026-13-45\n", "a: " + "[" * 3000 + "]" * 3000 + "\n"],
                         ids=["bad-timestamp", "deep-nesting"])
def test_expand_leaves_unconstructible_text_as_a_string(bad):
    assert expand(bad) == bad
