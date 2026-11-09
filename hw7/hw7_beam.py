import argparse
import posixpath
import re
import time

import apache_beam as beam
from apache_beam.io import fileio
from apache_beam.io.filesystems import FileSystems
from apache_beam.options.pipeline_options import PipelineOptions

LINK_RE = re.compile(r"<a\s[^>]*?href\s*=\s*[\"']?([^\"'\s>]+)", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]*>")
WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")
RESULTS = ["incoming_links", "outgoing_links", "trigrams"]


def read_file(readable):
    return posixpath.basename(readable.metadata.path), readable.read_utf8()


def links(page):
    return [posixpath.basename(h.split("#")[0].split("?")[0]) for h in LINK_RE.findall(page[1])]


def trigrams(page):
    words = WORD_RE.findall(TAG_RE.sub(" ", page[1]).lower())
    return [" ".join(words[i:i + 3]) for i in range(len(words) - 2)]


def rank_key(kv):
    return -kv[1], kv[0]


def format_top(rows, title, unit):
    lines = [title] + [f"{i}. {k}: {v} {unit}" for i, (k, v) in enumerate(rows, 1)]
    return "\n".join(lines)


def top5(pcoll, label, title, unit, output):
    return (pcoll
            | f"Top5 {label}" >> beam.combiners.Top.Of(5, key=rank_key, reverse=True)
            | f"Format {label}" >> beam.Map(format_top, title, unit)
            | f"Write {label}" >> beam.io.WriteToText(f"{output}/{label}", file_name_suffix=".txt",
                                                      num_shards=1, shard_name_template=""))


def build(p, input_pattern, output):
    pages = (p
             | "Match files" >> fileio.MatchFiles(input_pattern)
             | "Read files" >> fileio.ReadMatches()
             | "Name and text" >> beam.Map(read_file))

    incoming = (pages
                | "Link targets" >> beam.FlatMap(links)
                | "Count incoming" >> beam.combiners.Count.PerElement())
    top5(incoming, "incoming_links", "Top 5 files by incoming links", "incoming links", output)

    outgoing = pages | "Count outgoing" >> beam.Map(lambda page: (page[0], len(links(page))))
    top5(outgoing, "outgoing_links", "Top 5 files by outgoing links", "outgoing links", output)

    grams = (pages
             | "Trigrams" >> beam.FlatMap(trigrams)
             | "Count trigrams" >> beam.combiners.Count.PerElement())
    top5(grams, "trigrams", "Top 5 word trigrams", "times", output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="gs://skyvoong-cs528-hw2/files/*.html")
    parser.add_argument("--output", required=True)
    args, beam_args = parser.parse_known_args()
    options = PipelineOptions(beam_args, save_main_session=True)
    runner = options.get_all_options()["runner"] or "DirectRunner"

    start = time.time()
    with beam.Pipeline(options=options) as p:
        build(p, args.input, args.output.rstrip("/"))
    elapsed = time.time() - start

    for name in RESULTS:
        with FileSystems.open(f"{args.output.rstrip('/')}/{name}.txt") as f:
            print(f.read().decode().strip())
        print()
    print(f"Runner: {runner}")
    print(f"Input: {args.input}")
    print(f"Total time: {elapsed:.1f} seconds")


if __name__ == "__main__":
    main()