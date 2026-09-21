/**
 * Optional official marks. Both default to null, which means the original
 * ledger / scales / 24-spoke wheel artwork is used everywhere.
 *
 * Fill these ONLY with written authorisation from the owning body: the State
 * Emblem of India is legally protected, and the CAG logo is a government body's
 * insignia. When set, a mark is shown at fixed proportions with clear space,
 * never recoloured and never animated (only the glow / ring AROUND it moves).
 */
export const brandMarks = {
  emblem: null, // url | null
  cag: null,    // url | null
};

export const hasOfficialMark = () => Boolean(brandMarks.emblem || brandMarks.cag);

export const DISCLAIMER = 'Independent audit-assistance tool. Not an official government service.';
