from ecommerce_agent.auth.passwords import hash_password, verify_password


def test_hash_is_not_plaintext_and_verifies():
    password_hash = hash_password("s3cret-pw")
    assert password_hash != "s3cret-pw"
    assert password_hash.startswith("$argon2")
    assert verify_password("s3cret-pw", password_hash) is True


def test_verify_rejects_wrong_password():
    password_hash = hash_password("s3cret-pw")
    assert verify_password("wrong", password_hash) is False


def test_verify_rejects_malformed_hash():
    assert verify_password("anything", "not-a-hash") is False


def test_hashes_are_salted_and_differ():
    assert hash_password("same") != hash_password("same")
