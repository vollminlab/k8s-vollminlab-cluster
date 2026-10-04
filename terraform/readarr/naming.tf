# Ebook layout: /books/<Author>/<Book Title>/<Author> - <Book Title>.epub
# These values were set by hand in the UI and are declared here unchanged (read
# back from the live config 2026-10-04), so the first apply is a no-op. Same
# format as readarr-audio (terraform/readarr-audio/naming.tf).
# readarr_naming is a singleton: create updates the live config, delete only
# detaches it from state.
resource "readarr_naming" "this" {
  rename_books               = true
  replace_illegal_characters = true
  colon_replacement_format   = 4 # Smart
  author_folder_format       = "{Author Name}"
  standard_book_format       = "{Book Title}/{Author Name} - {Book Title}{ (PartNumber)}"
}
