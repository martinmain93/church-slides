//! The example hymns a brand-new library starts with.
//!
//! Four complete hymns whose words are public domain everywhere — published
//! long before the US cut-off, and by writers who died well over 70 years ago
//! (the rule in `sidecar/auto_presenter/hymns/rights.py`). The text is the
//! Open Hymnal Project's (openhymnal.org), converted once with
//! `auto_presenter.hymns`; the project dedicates its own transcriptions and
//! data to the public domain. Two hand corrections: "Holy, Holy, Holy" is
//! joined into its four real lines (the source's metre counts its musical
//! half-lines), and "every plea?" in "Abide With Me" — a lost dash in the
//! source — reads "every plea;" as hymnals print it.
//!
//! Any other hymn is added on demand from Library → Add hymns.

pub struct SeedHymn {
    pub id: &'static str,
    pub title: &'static str,
    pub author: &'static str,
    pub copyright: &'static str,
    /// Empty: each section once, in order.
    pub slide_order: &'static str,
    pub body: &'static str,
}

pub const SEED_HYMNS: &[SeedHymn] = &[
    SeedHymn {
        id: "song_amazing_grace",
        title: "Amazing Grace",
        author: "John Newton",
        copyright: "Words: John Newton, 1779; last verse anonymous, before 1829. Public domain.",
        slide_order: "",
        body: r#"[verse 1]
Amazing grace! How sweet the sound
That saved a wretch like me!
I once was lost, but now am found;
Was blind, but now I see.

[verse 2]
'Twas grace that taught my heart to fear,
And grace my fears relieved;
How precious did that grace appear
The hour I first believed.

[verse 3]
Through many dangers, toils and snares,
I have already come;
'Tis grace hath brought me safe thus far,
And grace will lead me home.

[verse 4]
The Lord has promised good to me,
His Word my hope secures;
He will my Shield and Portion be,
As long as life endures.

[verse 5]
Yea, when this flesh and heart shall fail,
And mortal life shall cease,
I shall possess, within the veil,
A life of joy and peace.

[verse 6]
The earth shall soon dissolve like snow,
The sun forbear to shine;
But God, Who called me here below,
Shall be forever mine.

[verse 7]
When we've been there ten thousand years,
Bright shining as the sun,
We've no less days to sing God's praise
Than when we'd first begun."#,
    },
    SeedHymn {
        id: "song_holy_holy_holy",
        title: "Holy, Holy, Holy",
        author: "Reginald Heber",
        copyright: "Words: Reginald Heber, 1826. Public domain.",
        slide_order: "",
        body: r#"[verse 1]
Holy, holy, holy! Lord God Almighty!
Early in the morning our song shall rise to Thee;
Holy, holy, holy, merciful and mighty!
God in three Persons, blessed Trinity!

[verse 2]
Holy, holy, holy! All the saints adore Thee,
Casting down their golden crowns around the glassy sea;
Cherubim and seraphim falling down before Thee,
Who was, and is, and evermore shall be.

[verse 3]
Holy, holy, holy! though the darkness hide Thee,
Though the eye of sinful man Thy glory may not see;
Only Thou art holy; there is none beside Thee,
Perfect in pow'r, in love, and purity.

[verse 4]
Holy, holy, holy! Lord God Almighty!
All Thy works shall praise Thy Name, in earth, and sky, and sea;
Holy, holy, holy; merciful and mighty!
God in three Persons, blessed Trinity!"#,
    },
    SeedHymn {
        id: "song_it_is_well",
        title: "It Is Well With My Soul",
        author: "Horatio Gates Spafford",
        copyright: "Words: Horatio G. Spafford, 1873. Public domain.",
        slide_order: "V1, C, V2, C, V3, C, V4, C, V5, C",
        body: r#"[verse 1]
When peace, like a river, attendeth my way,
When sorrows like sea billows roll;
Whatever my lot, Thou has taught me to say,
It is well, it is well, with my soul.

[chorus]
It is well, with my soul,
It is well, it is well with my soul.

[verse 2]
Though Satan should buffet, though trials should come,
Let this blest assurance control,
That Christ has regarded my helpless estate,
And hath shed His own blood for my soul.

[verse 3]
My sin, oh, the bliss of this glorious thought!
My sin, not in part but the whole,
Is nailed to the cross, and I bear it no more,
Praise the Lord, praise the Lord, O my soul!

[verse 4]
For me, be it Christ, be it Christ hence to live:
If Jordan above me shall roll,
No pang shall be mine, for in death as in life
Thou wilt whisper Thy peace to my soul.

[verse 5]
But, Lord, 'tis for Thee, for Thy coming we wait,
The sky, not the grave, is our goal;
Oh trump of the angel! Oh voice of the Lord!
Blessèd hope, blessèd rest of my soul!"#,
    },
    SeedHymn {
        id: "song_abide_with_me",
        title: "Abide With Me",
        author: "Henry F. Lyte",
        copyright: "Words: Henry F. Lyte, 1847. Public domain.",
        slide_order: "",
        body: r#"[verse 1]
Abide with me; fast falls the eventide;
The darkness deepens; Lord with me abide.
When other helpers fail and comforts flee,
Help of the helpless, O abide with me.

[verse 2]
Swift to its close ebbs out life's little day;
Earth's joys grow dim; its glories pass away;
Change and decay in all around I see;
O Thou who changest not, abide with me.

[verse 3]
Not a brief glance I beg, a passing word;
But as Thou dwell'st with Thy disciples, Lord,
Familiar, condescending, patient, free.
Come not to sojourn, but abide with me.

[verse 4]
Come not in terrors, as the King of kings,
But kind and good, with healing in Thy wings,
Tears for all woes, a heart for every plea;
Come, Friend of sinners, and thus bide with me.

[verse 5]
Thou on my head in early youth didst smile;
And, though rebellious and perverse meanwhile,
Thou hast not left me, oft as I left Thee,
On to the close, O Lord, abide with me.

[verse 6]
I need Thy presence every passing hour.
What but Thy grace can foil the tempter's power?
Who, like Thyself, my guide and stay can be?
Through cloud and sunshine, Lord, abide with me.

[verse 7]
I fear no foe, with Thee at hand to bless;
Ills have no weight, and tears no bitterness.
Where is death's sting? Where, grave, thy victory?
I triumph still, if Thou abide with me.

[verse 8]
Hold Thou Thy cross before my closing eyes;
Shine through the gloom and point me to the skies.
Heaven's morning breaks, and earth's vain shadows flee;
In life, in death, O Lord, abide with me."#,
    },
];
