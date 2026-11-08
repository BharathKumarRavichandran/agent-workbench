//! Closed path resources.
//!
//! Copied files and directories carry a validated SHA-256 digest. Directory
//! symlinks do not. Skills installation still emits only directory symlinks;
//! copied-resource mutation and multi-root state authorization are later units.
//! Serialization is the stable operation and state JSON shape.

use super::Result;

#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct ContentDigest(String);

impl ContentDigest {
    #[cfg_attr(not(test), allow(dead_code))]
    pub(super) fn parse(value: &str) -> Result<Self> {
        if value.len() == 64
            && value
                .bytes()
                .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
        {
            Ok(Self(value.to_owned()))
        } else {
            Err("digest must be SHA-256".into())
        }
    }

    pub(super) fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) enum PathResource {
    DirectorySymlink,
    #[cfg_attr(not(test), allow(dead_code))]
    FileCopy(ContentDigest),
    #[cfg_attr(not(test), allow(dead_code))]
    DirectoryCopy(ContentDigest),
}

impl PathResource {
    pub(super) fn kind(&self) -> &'static str {
        match self {
            Self::DirectorySymlink => "directory-symlink",
            Self::FileCopy(_) => "file-copy",
            Self::DirectoryCopy(_) => "directory-copy",
        }
    }

    pub(super) fn digest(&self) -> Option<&ContentDigest> {
        match self {
            Self::DirectorySymlink => None,
            Self::FileCopy(digest) | Self::DirectoryCopy(digest) => Some(digest),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn digest_rejects_non_sha256() {
        for value in [
            "",
            "ab",
            &"AB".repeat(32),
            &"gg".repeat(32),
            &format!("{}G", "a".repeat(63)),
        ] {
            assert!(ContentDigest::parse(value).is_err(), "{value}");
        }
        assert!(ContentDigest::parse(&"ab".repeat(32)).is_ok());
    }
}
