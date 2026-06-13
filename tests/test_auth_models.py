from ecommerce_agent.auth.models import Actor, Role, User


def test_role_values():
    assert Role.VIEWER == "viewer"
    assert Role.OPERATOR == "operator"


def test_user_and_actor_roundtrip():
    user = User(
        user_id="u1",
        username="alice",
        password_hash="$argon2id$...",
        role=Role.OPERATOR,
        spring_user_id=7,
        created_at="2026-06-13T00:00:00+00:00",
    )
    actor = Actor.from_user(user)
    assert actor.user_id == "u1"
    assert actor.username == "alice"
    assert actor.role == Role.OPERATOR
    assert actor.spring_user_id == 7
    assert not hasattr(actor, "password_hash")
