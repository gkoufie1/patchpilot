from patchpilot.scap import diff, parse_results

STDOUT_BEFORE = """--- Starting Evaluation ---

Title\tEnsure SELinux State is Enforcing
Rule\txccdf_org.ssgproject.content_rule_selinux_state
Ident\tCCE-84079-5
Result\tfail

Title\tEnsure Software Patches Installed
Rule\txccdf_org.ssgproject.content_rule_security_patches_up_to_date
Result\tfail

Title\tDisable SSH Root Login
Rule\txccdf_org.ssgproject.content_rule_sshd_disable_root_login
Result\tpass
"""

STDOUT_AFTER = """
Rule\txccdf_org.ssgproject.content_rule_selinux_state
Result\tfail
Rule\txccdf_org.ssgproject.content_rule_security_patches_up_to_date
Result\tpass
Rule\txccdf_org.ssgproject.content_rule_sshd_disable_root_login
Result\tfail
"""


def test_parse_reads_rule_result_pairs():
    assert parse_results(STDOUT_BEFORE) == {
        "xccdf_org.ssgproject.content_rule_selinux_state": "fail",
        "xccdf_org.ssgproject.content_rule_security_patches_up_to_date": "fail",
        "xccdf_org.ssgproject.content_rule_sshd_disable_root_login": "pass",
    }


def test_diff_reports_fixed_and_regressed():
    d = diff("app", parse_results(STDOUT_BEFORE), parse_results(STDOUT_AFTER))
    assert d.fixed == ["xccdf_org.ssgproject.content_rule_security_patches_up_to_date"]
    assert d.regressed == ["xccdf_org.ssgproject.content_rule_sshd_disable_root_login"]
    assert d.summary == "app: 2 -> 2 failing rules (1 fixed, 1 new)"
