# Renaming OFF. /audiobooks is Audiobookshelf's library in Author/Book layout, and a
# beets sweeper manages it too; Readarr must never reorganise existing folders.
# The formats below only apply if rename_books is ever turned on.
resource "readarr_naming" "this" {
  rename_books               = false
  replace_illegal_characters = true
  colon_replacement_format   = 0
  author_folder_format       = "{Author Name}"
  standard_book_format       = "{Book Title}/{Author Name} - {Book Title}{ (PartNumber)}"
}
