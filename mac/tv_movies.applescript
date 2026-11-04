-- Export Apple TV's Movies playlist as a tab-separated interchange stream.
-- Each row has five fields: title, year, genre, description, director.
-- No movie files are downloaded. The local TV app handles cloud library access.
-- Literal tabs/newlines in metadata are replaced with spaces for TSV safety.
-- This file intentionally does NOT invent a store ID from the local track ID.

on cleanField(inputValue)
	if inputValue is missing value then return ""
	set cleanedText to inputValue as text
	set previousDelimiters to AppleScript's text item delimiters
	repeat with separatorChar in {tab, return, linefeed}
		set AppleScript's text item delimiters to (contents of separatorChar)
		set textParts to text items of cleanedText
		set AppleScript's text item delimiters to " "
		set cleanedText to textParts as text
	end repeat
	set AppleScript's text item delimiters to previousDelimiters
	return cleanedText
end cleanField

on run
	set movieRows to {}
	tell application "TV"
		set movieTracks to (every track of playlist "Movies")
		repeat with movieTrack in movieTracks
			set movieTitle to my cleanField(name of movieTrack)
			if movieTitle is not "" then
				set movieYear to ""
				set movieGenre to ""
				set movieDescription to ""
				set movieDirector to ""
				try
					set movieYear to my cleanField(year of movieTrack)
				end try
				try
					set movieGenre to my cleanField(genre of movieTrack)
				end try
				try
					set movieDescription to my cleanField(description of movieTrack)
				end try
				try
					set movieDirector to my cleanField(director of movieTrack)
				end try
				set end of movieRows to movieTitle & tab & movieYear & tab & movieGenre & tab & movieDescription & tab & movieDirector
			end if
		end repeat
	end tell
	set previousDelimiters to AppleScript's text item delimiters
	set AppleScript's text item delimiters to linefeed
	set outputText to movieRows as text
	set AppleScript's text item delimiters to previousDelimiters
	return outputText
end run
