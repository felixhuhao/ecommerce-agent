from ecommerce_agent.grounding.model import Authority, Grounding, GroundingSource


def test_grounding_to_dict_roundtrips() -> None:
    grounding = Grounding(
        authority=Authority.AUTHORITATIVE,
        sources=[
            GroundingSource(
                span_id="s1",
                tool_name="get_statistics",
                args_summary="{}",
                result_summary="rows",
            )
        ],
    )

    data = grounding.to_dict()

    assert data["authority"] == "authoritative"
    assert data["sources"][0]["tool_name"] == "get_statistics"
    assert data["diagnostic"] is None
