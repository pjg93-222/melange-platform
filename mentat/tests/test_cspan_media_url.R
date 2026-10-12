script_argument <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
stopifnot(length(script_argument) == 1L)
script_path <- normalizePath(
  sub("^--file=", "", script_argument[[1L]]),
  winslash = "/",
  mustWork = TRUE
)
scraper_path <- normalizePath(
  file.path(dirname(script_path), "..", "..", "cspan", "cspan_scrape.R"),
  winslash = "/",
  mustWork = TRUE
)

# cspan_scrape.R is a script that runs a command on load, so evaluate only the
# functions under test. Nothing here starts a browser, ffmpeg, or the network.
scraper <- new.env()
for (expression in parse(scraper_path, keep.source = FALSE)) {
  if (
    is.call(expression) &&
      identical(as.character(expression[[1L]]), "<-") &&
      as.character(expression[[2L]])[[1L]] %in%
        c("media_url_for", "log_msg", "is_web_media_url", "download_audio")
  ) {
    eval(expression, scraper)
  }
}
stopifnot(is.function(scraper$is_web_media_url), is.function(scraper$download_audio))

accepted <- c(
  "https://m3u8-0.c-spanvideo.org/program/program.123.tsc.m3u8",
  "http://m3u8-0.c-spanvideo.org/program/program.123.tsc.m3u8",
  "HTTPS://cdn.example.org/a.mp4?token=abc&exp=1"
)
rejected <- list(
  "file:///C:/Windows/win.ini",
  "concat:file:///C:/a.ts|https://x.org/b.ts",
  "subfile,,start,0,end,0,,:C:/a.ts",
  "ftp://example.org/a.mp3",
  "//example.org/a.mp4",
  "javascript:alert(1)",
  " https://example.org/a.mp4",
  "https://example.org/a b.mp4",
  "https://example.org/a\".mp4",
  "https://example.org/a\\",
  "https://example.org/a\\b.mp4",
  "https://example.org/a\nfile:///etc/passwd",
  "https://",
  "",
  NA_character_,
  NULL,
  c("https://example.org/a", "https://example.org/b"),
  42L
)
stopifnot(
  all(vapply(accepted, scraper$is_web_media_url, logical(1L))),
  !any(vapply(rejected, scraper$is_web_media_url, logical(1L)))
)

# Non-ASCII lookalikes of the characters refused above (NBSP, fullwidth quote,
# backslash and @), plus zero-width space, RTL override and a Cyrillic homoglyph
# host, are refused whatever the string's encoding mark. Built from \u escapes so
# this file stays ASCII. A percent-encoded URL is still accepted.
lookalikes <- c(
  "https://x.org/a\u00a0b",
  "https://x.org/a\uff02b",
  "https://x.org/a\uff3cb",
  "https://x.org\uff20evil.example/a",
  "https://x.org/a\u200bb",
  "https://x.org/a\u202eb",
  "https://\u0430pple.example/a.mp4"
)
latin1_nbsp <- iconv("https://x.org/a\u00a0b", "UTF-8", "latin1")
bytes_nbsp <- "https://x.org/a\u00a0b"
Encoding(bytes_nbsp) <- "bytes"
invalid_utf8 <- paste0("https://x.org/a", rawToChar(as.raw(0xc3)), "b")
stopifnot(
  identical(Encoding(latin1_nbsp), "latin1"),
  identical(Encoding(bytes_nbsp), "bytes"),
  !any(vapply(
    c(lookalikes, latin1_nbsp, bytes_nbsp, invalid_utf8),
    scraper$is_web_media_url,
    logical(1L),
    USE.NAMES = FALSE
  )),
  scraper$is_web_media_url("https://x.org/a%20b?X-Amz-Signature=abc%2Fdef")
)

# download_audio: a refused URL never reaches ffmpeg; an accepted one reaches it
# with the protocol whitelist placed before -i. system2 is stubbed to record calls.
calls <- list()
scraper$OUT <- tempfile("cspan-test-")
scraper$FFMPEG <- "ffmpeg-not-run"
scraper$UA <- "test-agent"
scraper$has_flag <- function(name) FALSE
scraper$system2 <- function(command, args, ...) {
  calls[[length(calls) + 1L]] <<- args
  1L
}
environment(scraper$download_audio) <- scraper
scraper$log_msg <- function(...) invisible(NULL)

for (url in c("file:///C:/Windows/win.ini", "concat:a|b", "ftp://example.org/a.mp3")) {
  scraper$download_audio("p1", url)
}
stopifnot(identical(length(calls), 0L), !dir.exists(file.path(scraper$OUT, "audio")))

scraper$download_audio("p1", "https://m3u8-0.c-spanvideo.org/program/program.1.tsc.m3u8")
stopifnot(identical(length(calls), 1L))
args <- gsub("^\"|\"$|^'|'$", "", calls[[1L]])
whitelist <- match("-protocol_whitelist", args)
input <- match("-i", args)
stopifnot(
  !is.na(whitelist),
  !is.na(input),
  whitelist < input,
  identical(args[[whitelist + 1L]], "https,tls,tcp,crypto,http"),
  identical(args[[input + 1L]], "https://m3u8-0.c-spanvideo.org/program/program.1.tsc.m3u8")
)
unlink(scraper$OUT, recursive = TRUE)

cat("test_cspan_media_url: only http(s) media URLs accepted\n")
cat("test_cspan_media_url: refused URLs never reach ffmpeg\n")
cat("test_cspan_media_url: protocol whitelist precedes -i\n")
cat("test_cspan_media_url: PASS\n")
