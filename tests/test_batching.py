import copy
import unittest
from unittest.mock import patch

from sd_webui_batch.batching import split_payload_into_chunks


class SplitPayloadIntoChunksTests(unittest.TestCase):
    def test_splits_6000_single_image_iterations_into_60_requests(self):
        chunks = split_payload_into_chunks({"n_iter": 6000, "batch_size": 1})

        self.assertEqual(len(chunks), 60)
        self.assertTrue(all(chunk.payload["n_iter"] == 100 for chunk in chunks))
        self.assertEqual(chunks[0].ordinal, 1)
        self.assertEqual(chunks[0].total_chunks, 60)
        self.assertEqual((chunks[0].image_start, chunks[0].image_end), (1, 100))
        self.assertEqual((chunks[-1].image_start, chunks[-1].image_end), (5901, 6000))
        self.assertEqual(chunks[-1].total_images, 6000)

    def test_keeps_the_final_partial_chunk(self):
        chunks = split_payload_into_chunks({"n_iter": 250, "batch_size": 1})

        self.assertEqual([chunk.payload["n_iter"] for chunk in chunks], [100, 100, 50])
        self.assertEqual([chunk.image_count for chunk in chunks], [100, 100, 50])
        self.assertEqual(
            [(chunk.image_start, chunk.image_end) for chunk in chunks],
            [(1, 100), (101, 200), (201, 250)],
        )

    def test_accounts_for_batch_size_when_calculating_request_limit(self):
        chunks = split_payload_into_chunks({"n_iter": 60, "batch_size": 4})

        self.assertEqual([chunk.payload["n_iter"] for chunk in chunks], [25, 25, 10])
        self.assertEqual([chunk.image_count for chunk in chunks], [100, 100, 40])
        self.assertEqual(chunks[-1].total_images, 240)
        self.assertEqual((chunks[-1].image_start, chunks[-1].image_end), (201, 240))

    def test_offsets_fixed_seed_and_subseed_by_completed_image_count(self):
        chunks = split_payload_into_chunks(
            {"n_iter": 60, "batch_size": 4, "seed": 1234, "subseed": 5678}
        )

        self.assertEqual([chunk.payload["seed"] for chunk in chunks], [1234, 1334, 1434])
        self.assertEqual(
            [chunk.payload["subseed"] for chunk in chunks],
            [5678, 5778, 5878],
        )
        self.assertEqual(
            [chunk.completed_images_before for chunk in chunks],
            [0, 100, 200],
        )

    def test_keeps_main_seed_fixed_when_subseed_variation_is_enabled(self):
        chunks = split_payload_into_chunks(
            {
                "n_iter": 250,
                "batch_size": 1,
                "seed": 1234,
                "subseed": 5678,
                "subseed_strength": 0.2,
            }
        )

        self.assertEqual([chunk.payload["seed"] for chunk in chunks], [1234, 1234, 1234])
        self.assertEqual(
            [chunk.payload["subseed"] for chunk in chunks],
            [5678, 5778, 5878],
        )

    def test_resolves_random_seeds_once_and_keeps_one_request_sequence(self):
        with patch(
            "sd_webui_batch.batching.random.randrange",
            side_effect=[1234, 5678],
        ) as randrange:
            chunks = split_payload_into_chunks(
                {"n_iter": 250, "batch_size": 1, "seed": -1, "subseed": -1}
            )

        self.assertEqual([chunk.payload["seed"] for chunk in chunks], [1234, 1334, 1434])
        self.assertEqual(
            [chunk.payload["subseed"] for chunk in chunks],
            [5678, 5778, 5878],
        )
        self.assertEqual(randrange.call_count, 2)
        randrange.assert_called_with(4294967294)

    def test_resolves_missing_and_empty_seeds_like_forge(self):
        payload = {"n_iter": 101, "batch_size": 1, "subseed": ""}
        original = copy.deepcopy(payload)
        with patch(
            "sd_webui_batch.batching.random.randrange",
            side_effect=[10, 20],
        ):
            chunks = split_payload_into_chunks(payload)

        self.assertEqual([chunk.payload["seed"] for chunk in chunks], [10, 110])
        self.assertEqual([chunk.payload["subseed"] for chunk in chunks], [20, 120])
        self.assertEqual(payload, original)

    def test_random_variation_seed_keeps_main_seed_fixed(self):
        with patch(
            "sd_webui_batch.batching.random.randrange",
            side_effect=[1000, 2000],
        ):
            chunks = split_payload_into_chunks(
                {
                    "n_iter": 250,
                    "batch_size": 1,
                    "seed": -1,
                    "subseed": None,
                    "subseed_strength": 0.25,
                }
            )

        self.assertEqual([chunk.payload["seed"] for chunk in chunks], [1000, 1000, 1000])
        self.assertEqual(
            [chunk.payload["subseed"] for chunk in chunks],
            [2000, 2100, 2200],
        )

    def test_plan_only_split_does_not_consume_or_resolve_random_seeds(self):
        with patch("sd_webui_batch.batching.random.randrange") as randrange:
            chunks = split_payload_into_chunks(
                {"n_iter": 250, "batch_size": 1, "seed": -1},
                resolve_random_seeds=False,
            )

        randrange.assert_not_called()
        self.assertEqual([chunk.payload["seed"] for chunk in chunks], [-1, -1, -1])
        self.assertTrue(all("subseed" not in chunk.payload for chunk in chunks))

    def test_does_not_mutate_or_share_nested_input_payload_values(self):
        payload = {
            "n_iter": 250,
            "batch_size": 1,
            "seed": 10,
            "override_settings": {"grid_save": False},
            "alwayson_scripts": {"example": {"args": [1, 2]}},
        }
        original = copy.deepcopy(payload)

        chunks = split_payload_into_chunks(payload)
        chunks[0].payload["override_settings"]["grid_save"] = True
        chunks[0].payload["alwayson_scripts"]["example"]["args"].append(3)

        self.assertEqual(payload, original)
        self.assertEqual(chunks[1].payload["override_settings"]["grid_save"], False)
        self.assertEqual(chunks[1].payload["alwayson_scripts"]["example"]["args"], [1, 2])

    def test_allows_a_custom_image_limit(self):
        chunks = split_payload_into_chunks(
            {"n_iter": 12, "batch_size": 3}, max_images_per_request=10
        )

        self.assertEqual([chunk.payload["n_iter"] for chunk in chunks], [3, 3, 3, 3])
        self.assertEqual([chunk.image_count for chunk in chunks], [9, 9, 9, 9])

    def test_defaults_missing_webui_counts_to_one(self):
        chunks = split_payload_into_chunks({"prompt": "test"})

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].payload["n_iter"], 1)
        self.assertEqual(chunks[0].image_count, 1)

    def test_rejects_invalid_positive_integer_values(self):
        invalid_cases = [
            ({"n_iter": 0, "batch_size": 1}, 100),
            ({"n_iter": -1, "batch_size": 1}, 100),
            ({"n_iter": 1.5, "batch_size": 1}, 100),
            ({"n_iter": True, "batch_size": 1}, 100),
            ({"n_iter": 1, "batch_size": 0}, 100),
            ({"n_iter": 1, "batch_size": "1"}, 100),
            ({"n_iter": 1, "batch_size": 1}, 0),
            ({"n_iter": 1, "batch_size": 1}, True),
        ]

        for payload, limit in invalid_cases:
            with self.subTest(payload=payload, limit=limit):
                with self.assertRaises(ValueError):
                    split_payload_into_chunks(payload, limit)

    def test_rejects_batch_size_larger_than_request_limit(self):
        with self.assertRaisesRegex(ValueError, "batch_size cannot exceed"):
            split_payload_into_chunks(
                {"n_iter": 1, "batch_size": 101}, max_images_per_request=100
            )

    def test_rejects_request_limit_above_hard_cap(self):
        with self.assertRaisesRegex(ValueError, "hard limit of 100"):
            split_payload_into_chunks(
                {"n_iter": 6000, "batch_size": 1},
                max_images_per_request=101,
            )


if __name__ == "__main__":
    unittest.main()
