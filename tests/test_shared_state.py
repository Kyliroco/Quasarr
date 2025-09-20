import unittest

from quasarr.providers import shared_state


class IsValidReleasePostmanTests(unittest.TestCase):
    def test_postman_user_agent_treated_as_movie(self):
        title = "Chien - HDRIP (FRENCH)"
        self.assertTrue(
            shared_state.is_valid_release(
                title=title,
                request_from="PostmanRuntime/7.43.3",
                search_string="chien",
                season=None,
                episode=None,
            )
        )


if __name__ == "__main__":
    unittest.main()
